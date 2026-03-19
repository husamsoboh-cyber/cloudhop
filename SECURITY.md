# Security

## How CloudHop handles your data

CloudHop runs entirely on your computer. Your files transfer directly between cloud providers using rclone. No data passes through any CloudHop server.

## Security features

- Server binds to `127.0.0.1` only (localhost, not accessible from network)
- CSRF protection on all state-changing endpoints
- Host header validation (DNS rebinding protection)
- Input validation on all user inputs
- Credentials managed by rclone (CloudHop never stores passwords)

## Reporting a vulnerability

If you find a security issue, please email husamsoboh@gmail.com instead of opening a public issue. I'll respond within 48 hours.
