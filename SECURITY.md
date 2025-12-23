# Security Policy

## Supported Versions

We release security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| latest (main) | :white_check_mark: |
| 0.89.x  | :white_check_mark: |
| < 0.89  | :x:                |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue in Pulse OS, please report it responsibly.

### How to Report

**Email:** jeff@graystorm.com

**Please include:**
- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact
- Any suggested fixes (if available)

### What to Expect

- **Initial Response:** Within 48 hours
- **Status Updates:** Every 5-7 days until resolved
- **Resolution Timeline:** We aim to patch critical vulnerabilities within 7 days

### Security Best Practices for Users

1. **Keep Updated:** Always run the latest version from the main branch
2. **Secure Your Configuration:**
   - Never commit `pulse.conf` to version control
   - Use strong MQTT credentials
   - Restrict Home Assistant token permissions
3. **Network Security:**
   - Run Pulse OS on a trusted network
   - Use TLS/SSL for MQTT connections when possible
   - Keep your Raspberry Pi OS updated

### Disclosure Policy

- We will acknowledge your report within 48 hours
- We will provide regular updates on the fix progress
- We will credit you in the security advisory (unless you prefer to remain anonymous)
- We will not take legal action against researchers who follow responsible disclosure

## Security Scanning

This project uses automated security scanning:

- **Dependabot:** Automated dependency updates
- **CodeQL:** Semantic code analysis
- **Bandit:** Python security linting
- **pip-audit:** Dependency vulnerability scanning
- **OpenSSF Scorecard:** Supply chain security metrics

See our [Security Status](README.md#security-status) badges for current scan results.

## Secure development practices
- At least one primary maintainer is experienced with secure design for Python services and mitigations for common classes of vulnerabilities (injection, authn/z bypass, SSRF, insecure deserialization, path traversal, weak crypto, hardcoded secrets).
- We require static analysis (Ruff, CodeQL) and tests (`pytest`) on every PR and before releases; medium+ severity findings are fixed before publishing.
- Dynamic checks: the pytest suite (with assertions enabled) runs on every PR and main-branch push.
- Delivery integrity: source and releases are distributed via HTTPS or SSH; we do not fetch unsigned hashes over HTTP. Verify tags and checksums where applicable.
- Vulnerability response: initial response ≤14 days (target 48h) and no medium-or-higher vulnerability remains unpatched for >60 days; critical issues are addressed as quickly as possible.
- Secrets policy: we do not commit credentials; PRs are scanned for secrets. If a secret is exposed, rotate immediately and remove it from history.
