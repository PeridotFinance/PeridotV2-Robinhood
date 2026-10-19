"""Operator tools tested with mocked subprocess/cloud boundaries; no credentials accessed."""
import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import worker
import create_wallet
import provision

class ToolingTests(unittest.TestCase):
    def test_cloud_errors_never_echo_api_payload(self):
        secret='throwaway-test-marker'
        with mock.patch.object(provision.subprocess,'run',return_value=mock.Mock(returncode=1,stdout=secret,stderr=secret)):
            with self.assertRaisesRegex(RuntimeError,'DigitalOcean request failed') as caught:
                provision.doctl('apps','list',payload=secret)
            self.assertNotIn(secret,str(caught.exception))

    def test_spec_defaults_to_single_monitor_and_contains_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'keeper').mkdir()
            spec=json.loads((Path(__file__).parent/'app.json').read_text())
            (root/'keeper/app.json').write_text(json.dumps(spec))
            address='0x'+'11'*20
            (root/'keeper/manifest.json').write_text(json.dumps({'keeper':address}))
            with mock.patch.object(provision,'ROOT',root): made=provision.base_spec(address)
            app=made['workers'][0]
            self.assertEqual(app['instance_count'],1)
            env={e['key']:e['value'] for e in app['envs']}
            self.assertEqual(env['EXECUTE'],'false')
            self.assertEqual(json.loads(env['RAMSES_MANIFEST_JSON'])['keeper'],address)
            self.assertNotIn('KEYSTORE_PASSWORD',env)

    def test_secret_values_are_runtime_secret_type(self):
        spec={}; provision.set_env(spec,'KEYSTORE_PASSWORD','synthetic',secret=True)
        self.assertEqual(spec['envs'][0]['type'],'SECRET')
        self.assertEqual(spec['envs'][0]['scope'],'RUN_TIME')

    def test_wallet_creation_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'.keeper-signer').mkdir()
            with mock.patch.object(create_wallet,'ROOT',root), mock.patch.object(create_wallet.subprocess,'run') as run:
                with self.assertRaisesRegex(RuntimeError,'already exists'): create_wallet.main()
                run.assert_not_called()

    def test_enable_preflight_precedes_signer_access_and_cloud(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'keeper').mkdir()
            address='0x'+'11'*20
            (root/'keeper/manifest.json').write_text(json.dumps({'keeper':address}))
            with mock.patch.object(provision,'ROOT',root), mock.patch.object(provision,'wallet',return_value=address), mock.patch.object(provision.worker,'Chain'), mock.patch.object(provision.worker,'preflight',side_effect=worker.SafetyError('privileged')), mock.patch.object(provision,'doctl') as cloud:
                with self.assertRaisesRegex(worker.SafetyError,'privileged'): provision.enable()
                cloud.assert_not_called()

    def test_signer_address_and_environment_cleanup_without_wallet_files(self):
        keeper='0x'+'11'*20
        env={'KEYSTORE_JSON_BASE64':base64.b64encode(json.dumps({'version':3,'crypto':{'cipher':'synthetic'}}).encode()).decode(), 'KEYSTORE_PASSWORD':'synthetic-test-only'}
        # All filesystem/subprocess boundaries mocked. No keystore or password file is read.
        with mock.patch.dict(os.environ,env,clear=True), mock.patch.object(Path,'write_bytes'), mock.patch.object(Path,'write_text'), mock.patch.object(Path,'chmod') as chmod, mock.patch.object(worker.subprocess,'run',return_value=mock.Mock(returncode=0,stdout=keeper)):
            worker.prepare_signer('/unused',keeper)
            self.assertNotIn('KEYSTORE_PASSWORD',os.environ)
            self.assertNotIn('KEYSTORE_JSON_BASE64',os.environ)
            self.assertEqual(chmod.call_args_list,[mock.call(0o600),mock.call(0o600)])
        with mock.patch.dict(os.environ,env,clear=True), mock.patch.object(Path,'write_bytes'), mock.patch.object(Path,'write_text'), mock.patch.object(Path,'chmod'), mock.patch.object(worker.subprocess,'run',return_value=mock.Mock(returncode=0,stdout='0x'+'22'*20)):
            with self.assertRaisesRegex(worker.SafetyError,'does not belong'): worker.prepare_signer('/unused',keeper)

    def test_prepare_signer_rejects_missing_without_subprocess(self):
        with mock.patch.dict(os.environ,{},clear=True), mock.patch.object(worker.subprocess,'run') as run:
            with self.assertRaisesRegex(worker.SafetyError,'not provisioned'):
                worker.prepare_signer('/unused','0x'+'11'*20)
            run.assert_not_called()

if __name__=='__main__': unittest.main()
