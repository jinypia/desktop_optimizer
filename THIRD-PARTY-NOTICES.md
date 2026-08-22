# Third-party notices

Desktop Optimizer itself is released under the MIT License (see `LICENSE`).
It builds on the open-source components listed below. The published
installer and portable ZIP **bundle** these components, so their notices
travel with the binaries.

| Component | Version | License |
|---|---|---|
| [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/) — includes Qt libraries and shiboken6 | 6.11.x | LGPL v3 |
| [psutil](https://github.com/giampaolo/psutil) | 7.x | BSD 3-Clause |
| [pyqtgraph](https://www.pyqtgraph.org/) | 0.14.x | MIT |
| [NumPy](https://numpy.org/) | 2.x | BSD 3-Clause |
| [PyInstaller](https://pyinstaller.org/) (build tool; bootloader is bundled) | 6.x | GPL v2-or-later **with** the PyInstaller bootloader exception |
| [Inno Setup](https://jrsoftware.org/isinfo.php) (build tool only, not distributed) | 6.x | Inno Setup License |

## Qt / PySide6 (LGPL v3) — what this means for redistribution

The binaries ship Qt and PySide6 under the **LGPL v3**. To keep that
compliant:

- Qt and PySide6 are **unmodified** upstream builds, installed from PyPI
  (`PySide6-Essentials`). No patches are applied.
- They are bundled as **separate dynamic libraries** (`.dll` / `.pyd`
  inside `_internal\`), not statically linked, so a recipient may replace
  them with their own compatible build of the library.
- The LGPL v3 text and Qt's licensing terms are distributed with Qt itself
  and are available at <https://doc.qt.io/qtforpython/licenses.html> and
  <https://www.gnu.org/licenses/lgpl-3.0.html>.
- Corresponding source for Qt and PySide6 is available from the Qt
  Project: <https://download.qt.io/official_releases/QtForPython/> and
  <https://code.qt.io/cgit/pyside/pyside-setup.git/>.

If you fork this project and distribute your own binaries, keep this file
with them and continue to satisfy the LGPL terms above.

## PyInstaller

The frozen executable embeds PyInstaller's bootloader. PyInstaller grants a
specific exception allowing the bootloader to be linked into applications
released under **any** license, so bundling it does not impose GPL terms on
Desktop Optimizer. See
<https://pyinstaller.org/en/stable/license.html>.

## Not legal advice

This file is a good-faith summary to help downstream users comply. Consult
each project's own license text — and, where it matters commercially, a
lawyer — before redistributing.
