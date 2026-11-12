"""Detection patterns for security and privacy scanning."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    rule_id: str
    severity: str  # critical, high, medium, low, info
    category: str  # security, privacy, quality
    message: str
    file: str
    line: int
    snippet: str = ""
    fix_suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet,
            "fix_suggestion": self.fix_suggestion,
        }


@dataclass
class Pattern:
    id: str
    name: str
    severity: str
    category: str
    regex: str
    message: str
    fix_suggestion: str = ""
    file_types: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    # Code-only rules skip matches inside string literals and comments (where docs mention eval()).
    code_only: bool = False

    @property
    def compiled(self) -> re.Pattern:
        return re.compile(self.regex, re.IGNORECASE)


CREDENTIAL_PATTERNS = [
    Pattern(
        id="SEC001",
        name="hardcoded_api_key",
        severity="critical",
        category="security",
        regex=r"""(?:api[_-]?key|apikey)['"]?\s*[:=]\s*['"][a-zA-Z0-9_\-]{20,}['"]""",
        message="Hardcoded API key detected",
        fix_suggestion="Use environment variables: os.environ['API_KEY']",
    ),
    Pattern(
        id="SEC002",
        name="hardcoded_secret",
        severity="critical",
        category="security",
        regex=r"""(?:secret|password|passwd|pwd|token)['"]?\s*[:=]\s*['"][^'"]{8,}['"]""",
        message="Hardcoded secret/password detected",
        fix_suggestion="Use environment variables or a secrets manager",
        exclude_patterns=[r"example", r"placeholder", r"your[_-]", r"<.*>", r"test"],
    ),
    Pattern(
        id="SEC003",
        name="aws_access_key",
        severity="critical",
        category="security",
        regex=r"AKIA[0-9A-Z]{16}",
        message="AWS Access Key ID detected",
        fix_suggestion="Rotate immediately and use IAM roles or environment variables",
    ),
    Pattern(
        id="SEC004",
        name="private_key",
        severity="critical",
        category="security",
        regex=r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        message="Private key embedded in source code",
        fix_suggestion="Move to a secure key store, never commit private keys",
    ),
    Pattern(
        id="SEC005",
        name="firebase_config_exposed",
        severity="high",
        category="security",
        regex=r"""databaseURL['"]\s*:\s*['"]https://[a-z0-9-]+\.firebaseio\.com['"]""",
        message="Firebase config with database URL exposed in source",
        fix_suggestion="Use Firebase security rules AND environment variables for config",
    ),
    Pattern(
        id="SEC006",
        name="jwt_hardcoded",
        severity="critical",
        category="security",
        regex=r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}",
        message="Hardcoded JWT token detected",
        fix_suggestion="Never hardcode tokens; generate at runtime",
    ),
    Pattern(
        id="SEC007",
        name="connection_string",
        severity="high",
        category="security",
        regex=r"""(?:mongodb|postgres|mysql|redis)://[a-zA-Z0-9_]+:[^@\s]+@[^\s'"]+""",
        message="Database connection string with credentials in source",
        fix_suggestion="Use environment variables for connection strings",
    ),
]

INJECTION_PATTERNS = [
    Pattern(
        id="SEC010",
        name="sql_injection",
        code_only=True,
        severity="high",
        category="security",
        regex=r"""(?:execute|cursor\.execute|query)\s*\(\s*(?:f['\"]|['\"].*%s|['\"].*\+\s*|['\"].*\.format)""",
        message="Possible SQL injection: string interpolation in query",
        fix_suggestion="Use parameterized queries: cursor.execute('SELECT * WHERE id = %s', (id,))",
        file_types=[".py"],
    ),
    Pattern(
        id="SEC011",
        name="command_injection",
        code_only=True,
        severity="critical",
        category="security",
        regex=r"""(?:os\.system|os\.popen|subprocess\.call|subprocess\.run|subprocess\.Popen)\s*\(\s*(?:f['\"]|['\"].*\+|.*\.format)""",
        message="Possible command injection: user input in shell command",
        fix_suggestion="Use subprocess with a list of args, never shell=True with user input",
        file_types=[".py"],
    ),
    Pattern(
        id="SEC012",
        name="xss_risk",
        code_only=True,
        severity="high",
        category="security",
        regex=r"""(?:innerHTML|outerHTML|document\.write)\s*[=(]\s*(?:.*\+|.*\$\{|.*`|.*concat)""",
        message="Possible XSS: unescaped user input in DOM manipulation",
        fix_suggestion="Use textContent or a sanitization library",
        file_types=[".js", ".ts", ".jsx", ".tsx"],
    ),
    Pattern(
        id="SEC013",
        name="eval_usage",
        code_only=True,
        severity="high",
        category="security",
        regex=r"""\beval\s*\(""",
        message="Use of eval() is a security risk",
        fix_suggestion="Avoid eval(); use ast.literal_eval() for Python or JSON.parse() for JS",
        file_types=[".py", ".js", ".ts"],
    ),
    Pattern(
        id="SEC014",
        name="shell_true",
        code_only=True,
        severity="high",
        category="security",
        regex=r"""subprocess\.\w+\(.*shell\s*=\s*True""",
        message="subprocess with shell=True is dangerous with user input",
        fix_suggestion="Use subprocess.run(['cmd', 'arg1', 'arg2']) without shell=True",
        file_types=[".py"],
    ),
]

PRIVACY_PATTERNS = [
    Pattern(
        id="PRV001",
        name="email_in_source",
        severity="medium",
        category="privacy",
        regex=r"""['"][a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}['"]""",
        message="Email address hardcoded in source",
        fix_suggestion="Use configuration or environment variables for email addresses",
        exclude_patterns=[r"example\.com", r"test\.com", r"placeholder", r"noreply"],
    ),
    Pattern(
        id="PRV002",
        name="ip_address_hardcoded",
        severity="low",
        category="privacy",
        regex=r"""\b(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b""",
        message="Hardcoded IP address detected",
        fix_suggestion="Use configuration for server addresses",
        exclude_patterns=[r"127\.0\.0\.1", r"0\.0\.0\.0", r"localhost", r"255\.255"],
    ),
    Pattern(
        id="PRV003",
        name="pii_logging",
        code_only=True,
        severity="high",
        category="privacy",
        regex=r"""(?:\b(?:log|logger|logging|console)\.\w+|\blog|\bprint)\s*\(.*(?:password|email|ssn|credit.?card|phone.?number|social.?security)""",
        message="Possible PII being logged",
        fix_suggestion="Never log sensitive data; mask or hash before logging",
    ),
    Pattern(
        id="PRV004",
        name="cors_wildcard",
        severity="medium",
        category="privacy",
        regex=r"""(?:Access-Control-Allow-Origin|cors.*origin)['"]?\s*[:=]\s*['"]\*['"]""",
        message="CORS wildcard (*) allows any origin to access the API",
        fix_suggestion="Restrict CORS to specific trusted domains",
    ),
]

QUALITY_PATTERNS = [
    Pattern(
        id="QUA001",
        name="todo_fixme",
        severity="info",
        category="quality",
        regex=r"""#\s*(?:TODO|FIXME|HACK|XXX|BUG)\b""",
        message="Unresolved TODO/FIXME marker",
    ),
    Pattern(
        id="QUA002",
        name="debug_left_behind",
        code_only=True,
        severity="medium",
        category="quality",
        regex=r"""(?:\bconsole\.log\s*\(|(?<![\w.])print\s*\(|\bdebugger\b|\bbreakpoint\s*\(\)|\bpdb\.set_trace)""",
        message="Debug statement left in code",
        fix_suggestion="Remove debug statements before committing",
        file_types=[".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"],
        exclude_patterns=[r"logging", r"logger"],
    ),
    Pattern(
        id="QUA003",
        name="broad_exception",
        code_only=True,
        severity="medium",
        category="quality",
        regex=r"""except\s*(?:Exception|BaseException)?\s*:""",
        message="Overly broad exception handler hides bugs",
        fix_suggestion="Catch specific exceptions",
        file_types=[".py"],
    ),
]

ALL_PATTERNS = CREDENTIAL_PATTERNS + INJECTION_PATTERNS + PRIVACY_PATTERNS + QUALITY_PATTERNS
