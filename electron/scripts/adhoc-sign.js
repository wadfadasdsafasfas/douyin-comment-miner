// electron-builder afterSign 钩子：对产物做一次完整的 ad-hoc 深签名。
//
// 背景：不对 Mac 包做 Developer ID 签名时，electron-builder 的默认签名
// 与包内 CodeResources 不一致（spctl 报 "code has no resources but signature
// indicates they must be present"）。这种「签名坏损」状态叠加浏览器下载的
// com.apple.quarantine 隔离属性，macOS 会直接报「已损坏，无法打开」且没有任何
// 绕过入口。重新做一次一致的 ad-hoc 深签名后，至少能把「已损坏（无解）」
// 降级成「无法验证开发者（可在系统设置里放行）」。
// 彻底解决 = 上 Apple Developer 账号做 Developer ID 签名 + 公证（见 MIGRATION.md）。
const { execSync } = require('child_process');
const path = require('path');

module.exports = async function adhocSign(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  console.log(`[adhoc-sign] codesign --force --deep --sign - "${appPath}"`);
  execSync(`codesign --force --deep --sign - '${appPath.replace(/'/g, `'\\''`)}'`, {
    stdio: 'inherit',
  });
};
