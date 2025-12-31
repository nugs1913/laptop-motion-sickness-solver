import sys
import math
import socket
import json
import asyncio
import websockets
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, 
                               QMenu, QStyle, QWidget)
from PySide6.QtCore import Qt, QTimer, QPointF, QThread, Signal
from PySide6.QtGui import QPainter, QBrush, QColor, QCursor, QAction, QIcon, QPixmap, QActionGroup

# --- 기본 설정 ---
PORT = 8989
GRID_SPACING = 120
MAX_DOT_SIZE = 45
SAFE_RADIUS = 300
DAMPING = 0.95

# IP 주소 가져오기
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

# --- WebSocket 서버 스레드 (수정 완료 버전) ---
class WebSocketServerThread(QThread):
    data_received = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.loop = None

    async def handle_client(self, websocket):
        print(f"✅ 새로운 연결: {websocket.remote_address}")
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    x = float(data.get('x', 0))
                    y = float(data.get('y', 0))
                    self.data_received.emit(x, y)
                except (json.JSONDecodeError, ValueError):
                    pass
        except websockets.exceptions.ConnectionClosed:
            print("❌ 연결 종료됨")

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        async def start_server_task():
            print(f"=========================================")
            print(f"📡 WebSocket 서버 시작 (Port: {PORT})")
            print(f"👉 앱 주소: ws://{get_ip()}:{PORT}")
            print(f"=========================================")
            async with websockets.serve(self.handle_client, "0.0.0.0", PORT, ping_interval=None):
                await asyncio.Future()

        try:
            self.loop.run_until_complete(start_server_task())
        except RuntimeError:
            pass

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.wait()

# --- 메인 오버레이 윈도우 ---
class MotionOverlay(QMainWindow):
    def __init__(self):
        super().__init__()
        
        # 윈도우 설정
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool  # 작업 표시줄 아이콘 숨김
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        screen_geometry = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geometry)
        self.width_limit = screen_geometry.width()
        self.height_limit = screen_geometry.height()
        
        self.center_x = self.width_limit / 2
        self.center_y = self.height_limit / 2
        self.max_distance = math.hypot(self.center_x, self.center_y)

        # 변수 초기화
        self.velocity = QPointF(0, 0)
        self.total_offset = QPointF(0, 0)
        self.sensitivity = 15.0  # 기본 민감도

        # 보정 관련
        self.is_calibrating = True
        self.calibration_buffer_x = []
        self.calibration_buffer_y = []
        self.bias_x = 0.0
        self.bias_y = 0.0
        
        # 필터링
        self.target_accel_x = 0.0
        self.target_accel_y = 0.0
        self.filtered_accel_x = 0.0
        self.filtered_accel_y = 0.0

        # 서버 시작
        self.server = WebSocketServerThread()
        self.server.data_received.connect(self.on_sensor_data)
        self.server.start()

        # 타이머
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_physics)
        self.timer.start(16)

    def start_calibration(self):
        """보정 모드 재시작"""
        self.calibration_buffer_x = []
        self.calibration_buffer_y = []
        self.velocity = QPointF(0, 0)
        self.is_calibrating = True
        self.update() # 화면 갱신 (텍스트 표시용)
        print("🔄 센서 재보정 시작...")

    def set_sensitivity(self, value):
        """민감도 설정"""
        self.sensitivity = value
        print(f"🎚️ 민감도 변경됨: {self.sensitivity}")

    def on_sensor_data(self, x, y):
        if self.is_calibrating:
            self.calibration_buffer_x.append(x)
            self.calibration_buffer_y.append(y)
            if len(self.calibration_buffer_x) > 50:
                self.bias_x = sum(self.calibration_buffer_x) / len(self.calibration_buffer_x)
                self.bias_y = sum(self.calibration_buffer_y) / len(self.calibration_buffer_y)
                self.is_calibrating = False
                print("✅ 보정 완료")
            return

        adj_x = x - self.bias_x
        adj_y = y - self.bias_y

        deadzone = 0.03
        if abs(adj_x) < deadzone: adj_x = 0
        if abs(adj_y) < deadzone: adj_y = 0

        # 설정된 민감도(self.sensitivity) 사용
        self.target_accel_x = adj_x * self.sensitivity
        self.target_accel_y = -adj_y * self.sensitivity 

    def update_physics(self):
        if self.is_calibrating: return

        alpha = 0.08
        self.filtered_accel_x += (self.target_accel_x - self.filtered_accel_x) * alpha
        self.filtered_accel_y += (self.target_accel_y - self.filtered_accel_y) * alpha
        
        current_accel = QPointF(self.filtered_accel_x, self.filtered_accel_y)

        self.velocity += current_accel
        self.velocity *= DAMPING
        
        if abs(self.velocity.x()) < 0.05 and abs(self.velocity.y()) < 0.05:
             self.velocity = QPointF(0, 0)
        
        self.total_offset += self.velocity
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self.is_calibrating:
            painter.setPen(QColor(255, 100, 100))
            font = painter.font()
            font.setPointSize(24)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 
                             "센서 보정 중...\n폰을 움직이지 마세요")
            return

        start_x = (self.total_offset.x() % GRID_SPACING) - GRID_SPACING
        start_y = (self.total_offset.y() % GRID_SPACING) - GRID_SPACING

        for x in range(int(start_x), self.width_limit + GRID_SPACING, GRID_SPACING):
            for y in range(int(start_y), self.height_limit + GRID_SPACING, GRID_SPACING):
                dist_from_center = math.hypot(x - self.center_x, y - self.center_y)
                if dist_from_center < SAFE_RADIUS: continue 

                progress = (dist_from_center - SAFE_RADIUS) / (self.max_distance - SAFE_RADIUS)
                progress = max(0.0, min(1.0, progress))
                ratio = progress ** 1.5

                size = ratio * MAX_DOT_SIZE
                alpha = int(ratio * 100)

                color = QColor(200, 200, 200, alpha)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QPointF(x - size/2, y - size/2), size, size)

    def closeEvent(self, event):
        # 창 닫기 이벤트 무시 (트레이 종료로만 꺼짐)
        event.ignore()
        self.hide()

# --- 아이콘 생성 함수 ---
def create_tray_icon_pixmap():
    # 64x64 크기의 투명한 픽스맵 생성
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    
    # 분홍색 원 그리기
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(255, 105, 180))) # Hot Pink
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return pixmap

# --- 메인 실행부 ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 마지막 창이 닫혀도 앱이 종료되지 않도록 설정 (백그라운드 실행 필수)
    app.setQuitOnLastWindowClosed(False)

    window = MotionOverlay()
    window.show()

    # --- 시스템 트레이 설정 ---
    tray_icon = QSystemTrayIcon(QIcon(create_tray_icon_pixmap()), app)
    tray_icon.setToolTip("멀미 방지 오버레이")

    # 트레이 메뉴 생성
    menu = QMenu()

    # 1. 보이기/숨기기 액션
    action_toggle = QAction("오버레이 보이기/숨기기", app)
    action_toggle.triggered.connect(lambda: window.hide() if window.isVisible() else window.showFullScreen())
    menu.addAction(action_toggle)

    menu.addSeparator()

    # 2. 센서 재보정 액션
    action_calib = QAction("센서 다시 보정하기", app)
    action_calib.triggered.connect(window.start_calibration)
    menu.addAction(action_calib)

    # 3. 민감도 서브 메뉴
    sensitivity_menu = menu.addMenu("민감도 설정")
    sens_group = QActionGroup(app) # 하나만 선택되도록 그룹화

    # 민감도 옵션들 (텍스트, 값)
    sens_options = [
        ("매우 낮음 (5)", 5.0),
        ("낮음 (10)", 10.0),
        ("보통 (15)", 15.0),
        ("높음 (30)", 30.0),
        ("매우 높음 (50)", 50.0)
    ]

    for label, val in sens_options:
        action = QAction(label, app, checkable=True)
        if val == 15.0: action.setChecked(True) # 기본값 체크
        # 클로저 문제 해결을 위해 val=val 사용
        action.triggered.connect(lambda checked, v=val: window.set_sensitivity(v))
        sens_group.addAction(action)
        sensitivity_menu.addAction(action)

    menu.addSeparator()

    # 4. 종료 액션
    action_quit = QAction("종료", app)
    def quit_app():
        window.server.stop() # 서버 스레드 안전 종료
        app.quit()
    action_quit.triggered.connect(quit_app)
    menu.addAction(action_quit)

    # 메뉴를 트레이 아이콘에 설정
    tray_icon.setContextMenu(menu)
    
    # 트레이 아이콘 클릭 시 동작 (클릭하면 메뉴 나옴)
    # 더블 클릭하면 오버레이 토글
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if window.isVisible():
                window.hide()
            else:
                window.showFullScreen()
    
    tray_icon.activated.connect(on_tray_activated)
    tray_icon.show()

    sys.exit(app.exec())