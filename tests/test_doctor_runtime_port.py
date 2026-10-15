"""Port diagnostics without importing the live API or touching account data."""
import ast
import socket
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from tam import doctor


def isolated_app():
    # Execute the actual two endpoint definitions, omitting live DB initialization.
    path = Path(__file__).resolve().parents[1] / 'tam' / 'api.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
             and n.name in {'doctor_get', 'doctor_fix'}]
    app = FastAPI()
    scope = dict(app=app, Depends=Depends, Request=Request, Any=Any,
                 auth=lambda: None, __package__='tam')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return app


class RuntimePortTests(unittest.TestCase):
    def test_startup_keeps_warning_for_actual_occupied_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            check = doctor.check_port(False, port)
            self.assertEqual(check.status, doctor.WARN)
            self.assertIn(str(port), check.name)

    def test_available_startup_port(self):
        with patch.object(doctor.socket, 'socket') as create:
            create.return_value.__enter__.return_value.connect_ex.return_value = 1
            create.return_value.connect_ex.return_value = 1
            self.assertEqual(doctor.check_port(False, 8849).status, doctor.OK)

    def test_runtime_port_does_not_probe_or_stop_current_service(self):
        with patch.object(doctor.socket, 'socket') as probe:
            check = doctor.check_port(True, 8849, serving=True)
        self.assertEqual(check.status, doctor.OK)
        self.assertIn('8849', check.name)
        self.assertNotIn('换端口', check.hint)
        probe.assert_not_called()

    def test_http_get_and_fix_use_asgi_port_not_host_header(self):
        app = isolated_app()
        async def server_address(scope, receive, send):
            scope['server'] = ('127.0.0.1', 8849)
            await app(scope, receive, send)
        with patch.object(doctor, 'CHECKS', [doctor.check_port]):
            with TestClient(server_address) as client:
                for method, url in [('GET', '/api/doctor'), ('POST', '/api/doctor/fix')]:
                    with self.subTest(method=method):
                        response = client.request(method, url, headers={'host': 'example.invalid:9999'})
                        self.assertEqual(response.status_code, 200)
                        report = response.json()
                        self.assertEqual(report['checks'][0]['name'], '端口 8849')
                        self.assertEqual(report['checks'][0]['status'], doctor.OK)
                        self.assertEqual(report['warned'], [])

    def test_runtime_without_tcp_address_is_not_default_port_probe(self):
        with patch.object(doctor, 'CHECKS', [doctor.check_port]), patch.object(doctor.socket, 'socket') as probe:
            report = doctor.run_doctor(serving=True, port=None)
        self.assertEqual(report['checks'][0]['status'], doctor.OK)
        self.assertNotIn('8848', report['checks'][0]['name'])
        probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
