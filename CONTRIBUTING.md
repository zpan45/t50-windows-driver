# Contributing

Issues and pull requests are welcome. Please read [docs/development.md](docs/development.md) and [docs/protocol.md](docs/protocol.md) first.

## Before a PR

- Run `python -m unittest tests.test_core tests.test_ipp tests.test_winpaper -v`
- If you change HID framing or status bits, test on a real T50 (`python -m t50 probe` / `test`)
- If you change `layout_on_tape`, print a known 40×30 mm PDF at Actual size and say what you saw
- Do not wire `t50.winpaper` into setup

## Scope

Useful: Windows 11 quirks, other T50 tape sizes, HID reliability, docs.

Out of scope unless you have a device: other SUPVAN models, Linux (use [heeen/supvan-cups](https://github.com/heeen/supvan-cups)), macOS.
