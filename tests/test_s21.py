import contextlib
import csv
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import litevna_cli as cli


class FakeSerial:
    timeout = 1

    def __init__(self, records=()):
        self.records = list(records)
        self.buffer = b''
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)
        if data[:2] == bytes((cli.CMD_READFIFO, cli.REG_VALUES_FIFO)):
            count = data[2]
            self.buffer += b''.join(self.records[:count])
            del self.records[:count]

    def read(self, size):
        # Exercise fragmented serial reads too.
        size = min(size, 7)
        out, self.buffer = self.buffer[:size], self.buffer[size:]
        return out

    def reset_input_buffer(self):
        self.buffer = b''

    def close(self):
        self.closed = True


def record(index, fwd=100, reflect=20, transmit=50):
    return struct.pack('<iiiiiiH', fwd, fwd, reflect, -reflect,
                       transmit, transmit, index) + b'\0' * 6


class S21Tests(unittest.TestCase):
    def test_channel_and_fifo_complex_ratio(self):
        for channel, expected in [(cli.CHANNEL_S21, .5), (cli.CHANNEL_S11, -.2j)]:
            ser = FakeSerial([record(1), record(0)])
            with patch.object(cli.time, 'sleep'):
                freqs, data = cli.acquire_grid(ser, 1000, 100, 2, 2, cli.MODE_USB_UNCAL, channel)
            self.assertEqual(freqs, [1000, 1100])
            self.assertEqual(data, [expected, expected])
            self.assertIn(bytes((cli.CMD_WRITE1, cli.REG_CHANNEL_SELECT, channel)), ser.writes)

    def test_invalid_and_duplicate_records_are_skipped(self):
        ser = FakeSerial([record(9), record(0, fwd=0), record(1), record(1), record(0)])
        with patch.object(cli.time, 'sleep'):
            freqs, data = cli.acquire_s21_grid(ser, 1000, 100, 2, 2, cli.MODE_USB_CAL)
        self.assertEqual(data, [.5, .5])
        self.assertIn(bytes((cli.CMD_WRITE1, cli.REG_DATA_MODE, cli.MODE_USB_CAL)), ser.writes)

    def test_missing_fifo_points_fail(self):
        ser = FakeSerial([record(1)] * 8)
        with patch.object(cli.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'Brakuje'):
            cli.acquire_s21_grid(ser, 1000, 100, 2, 2, 0)

    def test_response_recovers_amplitude_and_phase(self):
        true = [.3 + .2j, -.1 + .4j]
        gain = [.5 - .3j, .7 + .2j]
        iso = [.01 + .02j, -.02 + .01j]
        thru = [g + i for g, i in zip(gain, iso)]
        measured = [g * d + i for g, d, i in zip(gain, true, iso)]
        for result, expected in zip(cli.apply_s21(measured, thru, iso), true):
            self.assertAlmostEqual(result, expected)
        self.assertEqual(cli.apply_s21(thru, thru, iso), [1, 1])
        self.assertEqual(cli.apply_s21(iso, thru, iso), [0, 0])

    def test_bad_reference_rejected(self):
        for thru, iso in [([0j], [0j]), ([1j], [1j]), ([complex('nan')], [0j]), ([1], [])]:
            with self.assertRaises(ValueError):
                cli.s21_tracking(thru, iso)
        with self.assertRaises(ValueError):
            cli.apply_s21([], [1], [0])

    def test_calibration_measurement_roundtrip(self):
        for with_isolation in (False, True):
            with self.subTest(isolation=with_isolation), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 's21.json'
                out = Path(tmp) / 'dut.csv'
                parser = cli.build_parser()
                argv = ['calibrate-s21', '--start', '1M', '--stop', '2M', '--points', '2', '--out', str(path)]
                if with_isolation:
                    argv.append('--isolation')
                freqs = [1000000, 2000000]
                iso = [.01j, .02j] if with_isolation else [0j, 0j]
                thru = [.8 + .1j, .7 + .2j]
                acquisitions = [(freqs, iso), (freqs, thru)] if with_isolation else [(freqs, thru)]
                ser = FakeSerial()
                info = {'hw': 2, 'fw_major': 2, 'fw_minor': 2}
                with patch.object(cli, 'connect', return_value=(ser, 'FAKE', info)), patch.object(cli, 'acquire_s21_grid', side_effect=acquisitions), patch('builtins.input'), patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_calibrate_s21(parser.parse_args(argv))
                self.assertTrue(ser.closed)
                obj, saved_thru, saved_iso = cli.load_s21_cal(path)
                self.assertEqual(saved_thru, thru)
                self.assertEqual(saved_iso, iso)
                self.assertEqual(obj['isolation_measured'], with_isolation)
                self.assertTrue(path.with_name('s21_THRU_raw.csv').exists())
                self.assertEqual(path.with_name('s21_ISOLATION_raw.csv').exists(), with_isolation)
                args = parser.parse_args(['measure-s21', '--cal', str(path), '--out', str(out)])
                measured = [(t - i) * .1 + i for t, i in zip(thru, iso)]
                with patch.object(cli, 'connect', return_value=(ser, 'FAKE', info)), patch.object(cli, 'acquire_s21_grid', return_value=(freqs, measured)) as acquire, patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_measure_s21(args)
                self.assertEqual(acquire.call_args.args[1:], (1000000, 1000000, 2, 2, cli.MODE_USB_UNCAL))
                with out.open() as f:
                    rows = list(csv.DictReader(f))
                self.assertAlmostEqual(float(rows[0]['S21_logmag_dB']), -20)
                self.assertAlmostEqual(float(rows[0]['insertion_loss_dB']), 20)
                self.assertNotIn('S11_re', rows[0])

    def test_invalid_calibration_file_rejected(self):
        valid = {'format': cli.S21_CAL_FORMAT,
                 'grid': {'start_hz': 1, 'step_hz': 1, 'points': 2, 'avg': 2},
                 'thru': [[1, 0], [1, 0]], 'isolation': [[0, 0], [0, 0]]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cal.json'
            for mutate in [lambda v: v.update(format='LiteVNA-Python-SOL-1port-v1'),
                           lambda v: v.update(thru=[[1, 0]]),
                           lambda v: v['grid'].update(points=2.5),
                           lambda v: v['grid'].update(avg=0)]:
                obj = json.loads(json.dumps(valid))
                mutate(obj)
                path.write_text(json.dumps(obj))
                with self.assertRaises(ValueError):
                    cli.load_s21_cal(path)

    def test_cleanup_on_interruption_and_acquisition_error(self):
        for failure in (KeyboardInterrupt(), RuntimeError('USB failed')):
            ser = FakeSerial()
            args = cli.build_parser().parse_args(['measure-s21', '--raw'])
            with patch.object(cli, 'connect', return_value=(ser, 'FAKE', {})), patch.object(cli, 'acquire_s21_grid', side_effect=failure), patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(type(failure)):
                cli.cmd_measure_s21(args)
            self.assertTrue(ser.closed)
            self.assertEqual(ser.writes[-2:], [b'\x20\x44\x00', b'\x20\x26\x02'])

    def test_device_cal_requires_explicit_ui_grid(self):
        parser = cli.build_parser()
        for command, runner in [('measure', cli.cmd_measure), ('measure-s21', cli.cmd_measure_s21)]:
            for suffix in [[], ['--device-cal', '--current'], ['--start', '1M', '--stop', '2M']]:
                with self.subTest(command=command, suffix=suffix):
                    args = parser.parse_args([command] + suffix)
                    with patch.object(cli, 'connect') as connect, self.assertRaisesRegex(ValueError, '--start'):
                        runner(args)
                    connect.assert_not_called()

    def test_device_cal_default_for_both_measurements(self):
        parser = cli.build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            grid = ['--start', '1M', '--stop', '2M', '--points', '2']
            s11_args = parser.parse_args(['measure'] + grid + ['--out', str(Path(tmp)/'s11.s1p')])
            with patch.object(cli, 'connect', return_value=(FakeSerial(), 'FAKE', {})), \
                 patch.object(cli, 'acquire_s11_grid', return_value=([1000000, 2000000], [0j, 0j])) as acquire, \
                 patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_measure(s11_args)
            self.assertEqual(acquire.call_args.args[1:], (1000000, 1000000, 2, 2, cli.MODE_USB_CAL))
            s21_args = parser.parse_args(['measure-s21'] + grid + ['--out', str(Path(tmp)/'s21.csv')])
            with patch.object(cli, 'connect', return_value=(FakeSerial(), 'FAKE', {})), \
                 patch.object(cli, 'acquire_s21_grid', return_value=([1000000, 2000000], [1, 1])) as acquire, \
                 patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_measure_s21(s21_args)
            self.assertEqual(acquire.call_args.args[1:], (1000000, 1000000, 2, 2, cli.MODE_USB_CAL))

    def test_s1p_output_rejected_before_connection(self):
        args = cli.build_parser().parse_args(['measure-s21', '--out', 'wrong.s1p'])
        with patch.object(cli, 'connect') as connect, self.assertRaises(ValueError):
            cli.cmd_measure_s21(args)
        connect.assert_not_called()

    def test_s11_sol_regression(self):
        A, B, C = .02 + .01j, .8 + .2j, .05 - .03j
        model = lambda g: (A + B*g) / (1 - C*g)
        load = cli.z_to_gamma(23.75)
        coeffs = cli.fit_sol_point(model(1), model(-1), model(load), load)
        self.assertAlmostEqual(cli.apply_sol_point(model(.2+.3j), *coeffs), .2+.3j)


if __name__ == '__main__':
    unittest.main()
