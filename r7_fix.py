import os
os.chdir('d:/Desktop/minicode')

# Fix agent_intelligence.py
with open('py-src/minicode/agent_intelligence.py', 'r', encoding='utf-8') as f:
    c = f.read()

old = '''    PATTERNS = {
        ErrorCategory.NETWORK: [
            "connection", "timeout", "network", "refused", "unreachable",
            "reset", "closed", "dns", "ssl", "certificate",
        ],
        ErrorCategory.PERMISSION: [
            "permission", "access denied", "unauthorized", "forbidden",
            "privilege", "not allowed", "restricted", "admin",
        ],
        ErrorCategory.RESOURCE: [
            "memory", "disk", "space", "resource", "quota", "limit",
            "exceeded", "out of", "no space", "too large",
        ],
        ErrorCategory.TIMEOUT: [
            "timeout", "timed out", "deadline", "expired", "took too long",
        ],
        ErrorCategory.LOGIC: [
            "invalid", "not found", "does not exist", "already exists",
            "bad request", "syntax", "parse", "format", "type error",
        ],
    }'''

new = '''    PATTERNS = {
        ErrorCategory.NETWORK: frozenset({
            "connection", "timeout", "network", "refused", "unreachable",
            "reset", "closed", "dns", "ssl", "certificate",
        }),
        ErrorCategory.PERMISSION: frozenset({
            "permission", "access denied", "unauthorized", "forbidden",
            "privilege", "not allowed", "restricted", "admin",
        }),
        ErrorCategory.RESOURCE: frozenset({
            "memory", "disk", "space", "resource", "quota", "limit",
            "exceeded", "out of", "no space", "too large",
        }),
        ErrorCategory.TIMEOUT: frozenset({
            "timeout", "timed out", "deadline", "expired", "took too long",
        }),
        ErrorCategory.LOGIC: frozenset({
            "invalid", "not found", "does not exist", "already exists",
            "bad request", "syntax", "parse", "format", "type error",
        }),
    }'''

c = c.replace(old, new)

with open('py-src/minicode/agent_intelligence.py', 'w', encoding='utf-8') as f:
    f.write(c)
print('agent_intelligence done')
