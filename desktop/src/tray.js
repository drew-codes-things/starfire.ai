function createTray({ Tray, Menu, nativeImage, iconPath, actions, shortcutLabel }) {
  const tray = new Tray(nativeImage.createFromPath(iconPath));
  tray.setToolTip('starfire');

  function refresh() {
    const quickLabel = shortcutLabel() ? `Quick ask (${shortcutLabel()})` : 'Quick ask';
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Open starfire', click: actions.open },
      { label: quickLabel, click: actions.quickAsk },
      { label: 'Settings', click: actions.settings },
      { type: 'separator' },
      { label: 'Quit', click: actions.quit },
    ]));
  }

  tray.on('click', actions.open);
  refresh();
  return { tray, refresh };
}

module.exports = { createTray };
