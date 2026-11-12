# loci-guard: ignore-file  (fixtures contain fake secrets on purpose)
"""Tests for the Shield module."""

import tempfile
from pathlib import Path

from loci_guard.shield.analyzer import ShieldAnalyzer
from loci_guard.shield.patterns import PRIVACY_PATTERNS, QUALITY_PATTERNS
from loci_guard.shield.security import SecurityScanner

# Built in pieces so GitHub push protection doesn't mistake this fake key for a real one.
FAKE_STRIPE_KEY = "sk_" + "live_" + "abcdefghijklmnopqrstuvwxyz123456"


def test_detects_hardcoded_api_key():
    code = '\nconfig = {\n    "api_key": "' + FAKE_STRIPE_KEY + '"\n}\n'
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "config.py")
    assert len(findings) >= 1
    assert any(f.rule_id == "SEC001" for f in findings)


def test_detects_aws_key():
    code = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "settings.py")
    assert any(f.rule_id == "SEC003" for f in findings)


def test_detects_sql_injection():
    code = '''
def get_user(name):
    cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")
'''
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "db.py")
    assert any(f.rule_id == "SEC010" for f in findings)


def test_detects_command_injection():
    code = '''
import os
def run(cmd):
    os.system(f"echo {cmd}")
'''
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "utils.py")
    assert any(f.rule_id == "SEC011" for f in findings)


def test_detects_eval():
    code = 'result = eval(user_input)'
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "handler.py")
    assert any(f.rule_id == "SEC013" for f in findings)


def test_detects_private_key():
    code = '-----BEGIN RSA PRIVATE KEY-----\nMIIE...'
    scanner = SecurityScanner()
    findings = scanner.scan_content(code, "deploy.py")
    assert any(f.rule_id == "SEC004" for f in findings)


def test_detects_pii_logging():
    code = 'logger.info(f"User password: {password}")'
    scanner = SecurityScanner(PRIVACY_PATTERNS)
    findings = scanner.scan_content(code, "auth.py")
    assert any(f.rule_id == "PRV003" for f in findings)


def test_detects_cors_wildcard():
    code = '''headers = {"Access-Control-Allow-Origin": "*"}'''
    scanner = SecurityScanner(PRIVACY_PATTERNS)
    findings = scanner.scan_content(code, "server.py")
    assert any(f.rule_id == "PRV004" for f in findings)


def test_excludes_example_emails():
    code = 'DEFAULT_EMAIL = "test@example.com"'
    scanner = SecurityScanner(PRIVACY_PATTERNS)
    findings = scanner.scan_content(code, "config.py")
    assert not any(f.rule_id == "PRV001" for f in findings)


def test_analyzer_full_scan():
    with tempfile.TemporaryDirectory() as tmp:
        vuln_file = Path(tmp) / "bad_code.py"
        vuln_file.write_text('''
API_KEY = "api_key": "''' + FAKE_STRIPE_KEY + '''"
password = "secret": "super_secret_password_12345"
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
''')
        analyzer = ShieldAnalyzer(tmp)
        report = analyzer.full_scan()
        assert report["total_findings"] >= 1


def test_analyzer_scan_content_blocks_critical():
    with tempfile.TemporaryDirectory() as tmp:
        analyzer = ShieldAnalyzer(tmp)
        code = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
        result = analyzer.scan_content(code, "config.py")
        assert result["blocked"] is True


def test_code_only_rules_ignore_strings_and_comments():
    code = '''
MESSAGE = "Never use eval() or subprocess with shell=True"
# eval(x) would be dangerous here
console.print("done")
'''
    findings = SecurityScanner().scan_content(code, "docs.py")
    assert not findings


def test_code_only_rules_still_catch_real_calls():
    code = 'result = eval(user_input)  # "eval" is fine in this comment\nprint(result)\n'
    scanner = SecurityScanner()
    scanner.patterns = scanner.patterns + QUALITY_PATTERNS
    ids = {f.rule_id for f in scanner.scan_content(code, "handler.py")}
    assert {"SEC013", "QUA002"} <= ids


def test_debug_print_rule_only_applies_to_code_files():
    scanner = SecurityScanner()
    scanner.patterns = list(QUALITY_PATTERNS)
    line = "run: python -c \"print('done')\""
    assert not any(f.rule_id == "QUA002" for f in scanner.scan_content(line, "ci.yml"))
    assert any(f.rule_id == "QUA002" for f in scanner.scan_content("print('done')", "tool.py"))


def test_partial_snippet_is_treated_as_code():
    snippet = "        return eval(payload)\n    else:"
    assert any(f.rule_id == "SEC013" for f in SecurityScanner().scan_content(snippet, "a.py"))


def test_ignore_file_only_applies_to_files_on_disk(tmp_path):
    body = '# loci-guard: ignore-file\nKEY = "AKIAIOSFODNN7EXAMPLE"\n'
    path = tmp_path / "fixture.py"
    path.write_text(body)
    scanner = SecurityScanner()
    assert scanner.scan_file(path) == []
    assert any(f.rule_id == "SEC003" for f in scanner.scan_content(body, "fixture.py"))


def test_analyzer_clean_code_passes():
    with tempfile.TemporaryDirectory() as tmp:
        analyzer = ShieldAnalyzer(tmp)
        code = '''
import os

def get_api_key():
    return os.environ.get("API_KEY")
'''
        result = analyzer.scan_content(code, "config.py")
        assert result["blocked"] is False
        assert result["total_findings"] == 0
