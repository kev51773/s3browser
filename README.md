# S3 Browser

Fast, strictly read-only Amazon S3 desktop client for high-throughput bulk downloads. Optimized for macOS and Windows with native keyboard ergonomics and in-app AWS IAM Identity Center (SSO) support.

## Features

- **Strictly Read-Only**: Issues only `s3:ListBucket` and `s3:GetObject`. Zero write, delete, or upload operations.
- **High-Throughput Downloader**: 30 concurrent chunk worker threads via `s3transfer.TransferManager`. Directory downloads begin immediately while directory scanning streams in background.
- **In-App AWS SSO**: 4-field setup (`start_url`, `sso_region`, `account_id`, `role_name`) with browser-based device authorization.
- **Desktop Ergonomics**:
  - Rubberband / marquee box drag-selection.
  - Native modifier keys (`Cmd`/`Ctrl` click, `Shift` range select, `Cmd+D` download, `Backspace` / `Cmd+Up` navigate up).
  - Drag-and-drop local directory onto transfer panel to set destination.
  - Cascading bookmarks menu with folder categories and drag-and-drop organization.
- **Offline Demo Mode**: Run with `--demo` to test all UI features and simulated transfers without AWS credentials.

## Quickstart

Requires Python 3.10+.

### macOS & Linux

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./run.sh
```

### Windows

```cmd
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
run.bat
```

## Launch Flags

- `run.bat --demo` / `./run.sh --demo`: Launch offline demo mode with mock datasets.

## Shortcuts

| Shortcut (Mac / Win) | Action |
|---|---|
| `Enter` / Double Click | Open folder |
| `Cmd+Up` / `Backspace` | Up to parent folder |
| `Cmd+A` / `Ctrl+A` | Select all |
| `Cmd+D` / `Ctrl+D` | Download selected items |
| `Cmd+B` / `Ctrl+B` | Bookmark current path |
| `Cmd+R` / `Ctrl+R` | Refresh view |

## License

[MIT](LICENSE)

