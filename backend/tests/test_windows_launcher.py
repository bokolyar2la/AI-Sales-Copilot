import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')
def test_connect_launcher_reaches_menu_without_api():
    result = subprocess.run(
        ['cmd.exe', '/d', '/c', 'Connect-model.cmd'], cwd=ROOT,
        input=b'0\r\n\r\n', stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=15,
    )
    output = result.stdout.decode('utf-8', errors='replace')
    assert 'Выберите вариант [1]' in output
    assert 'Изменений нет.' in output
    assert 'is not recognized' not in output

@pytest.mark.skipif(os.name != 'nt', reason='Windows checkout line endings')
def test_batch_files_have_windows_line_endings():
    for path in ROOT.glob('*.cmd'):
        raw = path.read_bytes()
        assert b'\r\n' in raw
        assert b'\n' not in raw.replace(b'\r\n', b'')
