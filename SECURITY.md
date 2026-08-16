# Security

This software talks to a USB printer using a reverse-engineered HID protocol and installs a **local** IPP listener on `127.0.0.1` only.

- Do not expose port 8631 on a public interface.
- Report protocol or HID issues via GitHub issues. There is no bounty program.
- The project is unofficial and unsigned. Corporate “block unsigned printers” policies may refuse the queue.
