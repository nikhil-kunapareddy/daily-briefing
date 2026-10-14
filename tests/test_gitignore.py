import pathlib

def test_gitignore_contains_entries():
    gitignore_path = pathlib.Path('.gitignore')
    assert gitignore_path.is_file(), '.gitignore file missing'
    content = gitignore_path.read_text()
    required = ['.venv/', '__pycache__/', '*.pyc', '.pytest_cache/']
    for entry in required:
        assert entry in content, f"Missing {entry} in .gitignore"
