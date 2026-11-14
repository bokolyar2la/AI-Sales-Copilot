from backend import configure_llm
from dotenv import dotenv_values

def test_failed_probe_does_not_save_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(configure_llm, 'ROOT', tmp_path)
    answers = iter(['1', 'да'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(configure_llm.getpass, 'getpass', lambda _: 'test-secret-only')
    async def fail():
        raise ValueError('test-secret-only')
    monkeypatch.setattr(configure_llm, 'probe', fail)
    for name in ['LLM_PROVIDER','LLM_BASE_URL','LLM_MODEL','LLM_API_KEY','LLM_TIMEOUT','LLM_MAX_TOKENS']:
        monkeypatch.setenv(name, '')
    configure_llm.main()
    assert not (tmp_path / '.env').exists()
    assert 'test-secret-only' not in capsys.readouterr().out

def test_success_preserves_database_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(configure_llm, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('DATABASE_PATH=data/custom.sqlite\n', encoding='utf-8')
    answers = iter(['1', 'да'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(configure_llm.getpass, 'getpass', lambda _: 'test-secret-only')
    async def success():
        return {}
    monkeypatch.setattr(configure_llm, 'probe', success)
    for name in ['LLM_PROVIDER','LLM_BASE_URL','LLM_MODEL','LLM_API_KEY','LLM_TIMEOUT','LLM_MAX_TOKENS']:
        monkeypatch.setenv(name, '')
    configure_llm.main()
    config = dotenv_values(tmp_path / '.env')
    assert config['DATABASE_PATH'] == 'data/custom.sqlite'
    assert config['LLM_MODEL'] == 'deepseek-flash'
    assert config['LLM_API_KEY'] == 'test-secret-only'
    assert 'test-secret-only' not in capsys.readouterr().out
