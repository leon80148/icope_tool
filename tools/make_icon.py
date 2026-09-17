"""產生程式圖示 icope_tool/resources/app.ico（青綠圓角方塊＋白色心跳線）。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from PIL import Image
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication(sys.argv[:1])
    svg = (ROOT / "icope_tool" / "resources" / "icons" / "heart-pulse.svg").read_text(encoding="utf-8")
    svg = svg.replace("currentColor", "#FFFFFF").replace('stroke-width="2"', 'stroke-width="2.2"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = 256
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(8, 8, size - 16, size - 16), 56, 56)
    painter.fillPath(path, QColor("#0F766E"))
    renderer.render(painter, QRectF(48, 52, size - 96, size - 96))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    pil = Image.open(io.BytesIO(bytes(buffer.data())))
    target = ROOT / "icope_tool" / "resources" / "app.ico"
    pil.save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    pil.save(ROOT / "icope_tool" / "resources" / "app.png")
    print("wrote", target)
    del app


if __name__ == "__main__":
    main()
