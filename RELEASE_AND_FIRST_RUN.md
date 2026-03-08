# SkimClass 发布与首次运行说明

## 你需要给用户的文件
- macOS: `dist/SkimClass-macOS.dmg`
- Windows: `dist/SkimClass/SkimClass.exe`（或整个 `dist/SkimClass/` 目录）

`build_macos_dmg.sh` 和 `build_windows_exe.bat` 是开发者打包脚本，不是给最终用户运行的文件。

## 我这次检查到的状态
- 已成功生成 `dist/SkimClass-macOS.dmg`。
- 当前 mac 产物是 ad-hoc 签名，`spctl` 对 `.app` 和 `.dmg` 都显示 `rejected`。
- 这意味着部分用户首次打开时会看到“无法验证开发者/已损坏”类提示。

## 面向最终用户的首次启动步骤
1. 下载并双击 `SkimClass-macOS.dmg`。
2. 将 `SkimClass.app` 拖到 `Applications`。
3. 在 `Applications` 中找到 `SkimClass.app`，右键点击，选择“打开”。
4. 弹窗出现后再次点击“打开”。
5. 首次录制时授予权限：
   - 麦克风
   - 屏幕录制（系统设置 -> 隐私与安全 -> 屏幕录制）
6. 在应用内填写 API Key 后开始使用。

## 如果用户仍被拦截（macOS）
1. 打开“系统设置 -> 隐私与安全”。
2. 在底部找到被拦截的 `SkimClass`，点击“仍要打开”。
3. 回到应用再次打开。

## 开发者本地打包命令
### macOS
```bash
chmod +x build_macos_dmg.sh
./build_macos_dmg.sh
```

### Windows（需在 Windows 机器运行）
```bat
build_windows_exe.bat
```

## 正式对外发布建议
- 建议做 Developer ID 签名 + notarization，再分发 `.dmg`。
- 做完后用户体验会从“可能被拦截”变成“双击即开”。
