# T50 Label（中文）

这是 **SUPVAN T50M Pro** / **Katasymbol T50M Pro** 的非官方 Windows 打印方案。英文总览见仓库根目录 [README.md](../README.md)。

官方 Windows 驱动（`Supvan_T50_Printer` + `Supvan_T50_Service`）基本不能当正常打印机用。本程序会在系统里登记一台名叫 **T50 Label** 的打印机，Word、Chrome、Acrobat 都可以直接打。

**与 SUPVAN / Katasymbol / Microsoft 无关。** 协议来自社区逆向，风险自负。

## 最简单：下载 exe

不需要安装 Python，也不需要克隆源码。

1. 打开 [GitHub Releases](https://github.com/zpan45/t50-windows-driver/releases)，下载最新的 `T50Label.exe`。
2. 双击运行。第一次会弹出 UAC，请允许（用来登记系统打印机）。
3. **T50 Label 窗口要一直开着才能打。**
4. 在 Word / Chrome / Acrobat 里把打印机选成 **T50 Label**。

Acrobat：把 PDF 页面做成标签尺寸（例如 40×30 mm），打印时选 **实际大小 / Actual size**，不要选适合纸张。Windows 打印对话框里纸张仍是 **A4 或 Letter**。这是微软 IPP 类驱动的限制，修不好。程序会从 A4 页面上裁出标签再打到胶带上。

官方 **Katasymbol Editor** 和本程序不能同时占用 USB。请先退出官方软件；本程序启动时会停掉 `Supvan_T50_Service`。

勾选 **Start with Windows** 可开机启动。

**Repair printer… / 安装/修复打印机…** 只做一件事：重新登记 Windows 队列。打印对话框里找不到这台打印机时再点。它不会改纸张列表，也不会让字更清晰。

## 从源码安装（开发者）

需要 Windows 10/11、Python 3.10+、USB 连接打印机。

```powershell
git clone https://github.com/zpan45/t50-windows-driver.git
cd t50-windows-driver
py -m pip install -r requirements.txt
```

双击 `start.bat`，或：

```powershell
pythonw -m t50
```

第一次运行同样会弹出 UAC，用来登记系统打印机，并停掉官方占用 HID 的服务。窗口同样要一直开着。

卸载：

```powershell
python -m t50 uninstall
```

### 自己打包 exe

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

会生成 `dist\T50Label.exe`。一般用户请直接用 Releases 里的文件，不必自己打包。

## 日志

```text
%LOCALAPPDATA%\T50Label\t50.log
```

## 已知限制

- 打印对话框没有 40×30 mm 纸张（只有 A4/Letter）
- 打印头 203 DPI，热敏不可能像激光那样锐利
- 未做 WHQL 签名
