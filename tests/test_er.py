import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import litevna_cli as cli
from test_s21 import FakeSerial, record


def embed(s11, s21, s12, s22, directivity, source, reflection, transmission, load, isolation):
    # Forward 6-term VNA model, independent of the calibration implementation.
    determinant = (1-source*s11)*(1-load*s22)-source*load*s12*s21
    m11 = directivity + reflection*(s11*(1-load*s22)+load*s12*s21)/determinant
    m21 = isolation + transmission*s21/determinant
    return m11, m21


class EnhancedResponseTests(unittest.TestCase):
    def setUp(self):
        self.freqs = [1_000_000, 2_000_000]
        self.ed = [.03+.02j, -.02+.01j]
        self.es = [.2-.1j, -.1+.15j]
        self.er = [.8+.1j, .7-.2j]
        self.et = [.6-.2j, .9+.1j]
        self.iso = [.001+.002j, -.002+.003j]

    def raw(self, s11, s21, s12=0, s22=0, load=0):
        return [embed(s11, s21, s12, s22, self.ed[i], self.es[i],
                      self.er[i], self.et[i], load, self.iso[i]) for i in range(2)]

    def coefficients(self, load_r=50):
        gl = cli.z_to_gamma(load_r)
        return [cli.fit_sol_point(self.raw(1, 0)[i][0], self.raw(-1, 0)[i][0],
                                 self.raw(gl, 0)[i][0], gl) for i in range(2)]

    def test_both_channels_from_same_fifo_records(self):
        ser = FakeSerial([record(1), record(0)])
        with patch.object(cli.time, 'sleep'):
            freqs, s11, s21 = cli.acquire_s11_s21_grid(ser, 100, 10, 2, 2, 0)
        self.assertEqual(freqs, [100, 110])
        self.assertEqual(s11, [-.2j, -.2j])
        self.assertEqual(s21, [.5, .5])
        self.assertIn(b'\x20\x44\x00', ser.writes)
        self.assertEqual(sum(w[:2] == b'\x18\x30' for w in ser.writes), 1)

    def test_source_match_correction_amplitude_and_phase(self):
        for load_r in [50, 23.75]:
            for s11, s21 in [(0, 1), (.65+.2j, .3-.4j), (-.5j, -.1+.2j), (.2, 0)]:
                with self.subTest(load_r=load_r, s11=s11):
                    thru = self.raw(0, 1, 1, 0)
                    dut = self.raw(s11, s21)
                    result11, result21 = cli.apply_er(
                        [v[0] for v in dut], [v[1] for v in dut],
                        [v[1] for v in thru], self.iso, [v[0] for v in thru], self.coefficients(load_r))
                    for a, b in zip(result11, result21):
                        self.assertAlmostEqual(a, s11)
                        self.assertAlmostEqual(b, s21)
                    if s11 != 0 and s21 != 0:
                        old = cli.apply_s21([v[1] for v in dut], [v[1] for v in thru], self.iso)
                        self.assertGreater(abs(old[0]-s21), .001)

    def test_receiver_mismatch_in_thru_and_residual_dut_error(self):
        load = .15+.1j
        thru = self.raw(0, 1, 1, 0, load)
        s11, s21, s12, s22 = .3+.1j, .4-.2j, .35-.1j, -.2+.3j
        dut = self.raw(s11, s21, s12, s22, load)
        result11, result21 = cli.apply_er([v[0] for v in dut], [v[1] for v in dut],
                                         [v[1] for v in thru], self.iso,
                                         [v[0] for v in thru], self.coefficients())
        # ER eliminates source match, but cannot fully remove receiver loading.
        for a, b in zip(result11, result21):
            self.assertAlmostEqual(a, s11+s12*s21*load/(1-load*s22))
            self.assertAlmostEqual(b, s21/(1-load*s22))

    def make_cal(self, path, isolation=True):
        args = cli.build_parser().parse_args([
            'calibrate-er', '--start', '1M', '--stop', '2M', '--points', '2',
            '--load', '50', '--out', str(path)] + (['--isolation'] if isolation else []))
        thru = self.raw(0, 1, 1)
        ser = FakeSerial()
        with patch.object(cli, 'connect', return_value=(ser, 'FAKE', {})), \
             patch.object(cli, 'acquire_s11_grid', side_effect=[(self.freqs, [v[0] for v in self.raw(g, 0)]) for g in [1, -1, 0]]), \
             patch.object(cli, 'acquire_s21_grid', return_value=(self.freqs, self.iso)) as iso_acquire, \
             patch.object(cli, 'acquire_s11_s21_grid', return_value=(self.freqs, [v[0] for v in thru], [v[1] for v in thru])) as both, \
             patch('builtins.input') as prompt, patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_calibrate_er(args)
        self.assertEqual(prompt.call_count, 5 if isolation else 4)
        self.assertEqual(iso_acquire.call_count, int(isolation))
        self.assertEqual(both.call_args.args[1:], (1000000, 1000000, 2, 2, 0))
        self.assertTrue(ser.closed)
        self.assertEqual(ser.writes[-2:], [b'\x20\x44\x00', b'\x20\x26\x02'])
        return cli.load_s21_cal(path)

    def test_cli_roundtrip_common_file_and_raw_audit_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            calpath = Path(tmp)/'er.json'
            obj, thru, iso = self.make_cal(calpath)
            self.assertEqual(obj['format'], cli.ER_CAL_FORMAT)
            obj11, coeffs = cli.load_cal(calpath)
            self.assertEqual(obj, obj11)
            self.assertEqual(len(coeffs), 2)
            for suffix in ['_OPEN_raw.s1p', '_SHORT_raw.s1p', '_LOAD_raw.s1p', '_THRU_S11_raw.s1p', '_THRU_raw.csv', '_ISOLATION_raw.csv']:
                self.assertTrue((Path(tmp)/('er'+suffix)).exists())
            out = Path(tmp)/'dut.csv'
            args = cli.build_parser().parse_args(['measure-s21', '--cal', str(calpath), '--avg', '8', '--out', str(out)])
            dut = self.raw(.5+.2j, .1-.1j)
            with patch.object(cli, 'connect', return_value=(FakeSerial(), 'FAKE', {})), \
                 patch.object(cli, 'acquire_s11_s21_grid', return_value=(self.freqs, [v[0] for v in dut], [v[1] for v in dut])) as both, \
                 patch.object(cli, 'acquire_s21_grid') as single, patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_measure_s21(args)
            single.assert_not_called()
            self.assertEqual(both.call_args.args[1:], (1000000, 1000000, 2, 8, 0))
            with out.open() as f:
                rows = list(csv.DictReader(f))
            self.assertAlmostEqual(float(rows[0]['S21_re']), .1)
            self.assertAlmostEqual(float(rows[0]['S21_im']), -.1)
            self.assertIn('Enhanced Response', rows[0]['source'])
            self.assertTrue(out.with_name('dut_s11.s1p').exists())
            # Existing measure command can use the same ER file for S11 alone.
            args = cli.build_parser().parse_args(['measure', '--cal', str(calpath), '--out', str(Path(tmp)/'s11.s1p')])
            with patch.object(cli, 'connect', return_value=(FakeSerial(), 'FAKE', {})), patch.object(cli, 'acquire_s11_grid', return_value=(self.freqs, [v[0] for v in dut])), patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_measure(args)

    def test_no_isolation_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'er.json'
            obj, thru, iso = self.make_cal(path, isolation=False)
            self.assertEqual(iso, [0j, 0j])
            self.assertFalse((Path(tmp)/'er_ISOLATION_raw.csv').exists())
            for mutate in [lambda d: d.update(coefficients=[]),
                           lambda d: d.update(thru_s11=[]),
                           lambda d: d['coefficients'][0].__setitem__(0, float('nan')),
                           lambda d: d['coefficients'].__setitem__(0, [0]*6),
                           lambda d: d.update(z0_ohm=75)]:
                broken = json.loads(json.dumps(obj))
                mutate(broken)
                path.write_text(json.dumps(broken))
                args = cli.build_parser().parse_args(['measure-s21', '--cal', str(path)])
                with patch.object(cli, 'connect') as connect, self.assertRaises(ValueError):
                    cli.cmd_measure_s21(args)
                connect.assert_not_called()

    def test_interruption_restores_device(self):
        args = cli.build_parser().parse_args(['calibrate-er'])
        ser = FakeSerial()
        with patch.object(cli, 'connect', return_value=(ser, 'FAKE', {})), patch('builtins.input', side_effect=KeyboardInterrupt), patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            cli.cmd_calibrate_er(args)
        self.assertTrue(ser.closed)
        self.assertEqual(ser.writes[-2:], [b'\x20\x44\x00', b'\x20\x26\x02'])

    def test_invalid_load_rejected_before_connection(self):
        for value in ['0', '-50', 'nan', 'inf']:
            args = cli.build_parser().parse_args(['calibrate-er', '--load', value])
            with patch.object(cli, 'connect') as connect, self.assertRaises(ValueError):
                cli.cmd_calibrate_er(args)
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
