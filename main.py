import sys
import math
import socket
import json
from collections import deque
from PySide6.QtWidgets import (QApplication, QMainWindow, QSystemTrayIcon, 
                               QMenu, QWidget)
from PySide6.QtCore import Qt, QTimer, QPointF, QThread, Signal
from PySide6.QtGui import QPainter, QBrush, QColor, QAction, QIcon, QPixmap, QActionGroup, QTransform

# --- 기본 설정 ---
PORT = 8989
GYRO_SENSITIVITY = 40.0 
ACCEL_SENSITIVITY = 150.0

# [수정] 줌 민감도: 가속도 변화에 민감하게 반응하도록 설정
ZOOM_SENSITIVITY = 0.5 

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

class UdpServerThread(QThread):
    data_received = Signal(float, float, float, float, float, float)

    def __init__(self):
        super().__init__()
        self.running = True
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("0.0.0.0", PORT))
        except Exception:
            return

        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                text = data.decode('utf-8').strip()
                jd = json.loads(text)
                
                # 소수점 2째 자리 반올림 (양자화) - 노이즈 1차 제거
                def quantize(val):
                    return round(float(val), 2)

                self.data_received.emit(
                    quantize(jd.get('gx', 0.0)), 
                    quantize(jd.get('gy', 0.0)), 
                    quantize(jd.get('gz', 0.0)),
                    quantize(jd.get('ax', 0.0)), 
                    quantize(jd.get('ay', 0.0)), 
                    quantize(jd.get('az', 0.0))
                )
            except:
                pass

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        self.wait()

class MotionOverlay(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self.width_limit = screen.width()
        self.height_limit = screen.height()
        self.center_x = self.width_limit / 2
        self.center_y = self.height_limit / 2
        self.diag_len = math.hypot(self.width_limit, self.height_limit)

        self.pos_x = 0.0
        self.pos_y = 0.0
        self.rotation_angle = 0.0 
        self.scale_factor = 1.0
        
        self.current_opacity = 0.0
        self.target_opacity = 0.0

        self.is_calibrating = True
        self.calib_data = []
        
        # [핵심] 기준값(Bias) 변수
        # 자이로(회전)는 고정된 기준값을 씁니다. (내가 멈추면 값도 0이어야 함)
        self.bias_gy = 0.0
        
        # 가속도(위치/크기)는 '유동적 기준값'을 씁니다. (상황에 따라 0점이 변함)
        # 이를 통해 폰을 떨어뜨려도 잠시 후엔 그 상태가 0점이 됩니다.
        self.running_bias_ax = 0.0
        self.running_bias_ay = 9.8
        self.running_bias_az = 0.0
        self.bias_angle = 0.0

        # 이동 평균 필터 버퍼
        self.window_size = 6
        self.buf_ax = deque(maxlen=self.window_size)
        self.buf_ay = deque(maxlen=self.window_size)
        self.buf_az = deque(maxlen=self.window_size)
        self.buf_gy = deque(maxlen=self.window_size)
        
        self.f_ax = 0.0
        self.f_ay = 0.0
        self.f_az = 0.0
        self.f_gy = 0.0

        self.server = UdpServerThread()
        self.server.data_received.connect(self.on_sensor_data)
        self.server.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_physics)
        self.timer.start(16) 

    def start_calibration(self):
        self.calib_data = []
        self.is_calibrating = True
        self.update()

    def set_gyro_sensitivity(self, value):
        global GYRO_SENSITIVITY
        GYRO_SENSITIVITY = value * 10.0

    def on_sensor_data(self, gx, gy, gz, ax, ay, az):
        # 1. 초기 보정
        if self.is_calibrating:
            self.calib_data.append((gx, gy, gz, ax, ay, az))
            if len(self.calib_data) > 30:
                avgs = [sum(col) / len(col) for col in zip(*self.calib_data)]
                
                # 자이로 Bias는 고정 (회전 멈춤을 0으로 인식)
                self.bias_gy = avgs[1] 
                
                # 가속도 Bias는 초기값 설정 후 계속 변함
                self.running_bias_ax = avgs[3]
                self.running_bias_ay = avgs[4]
                self.running_bias_az = avgs[5]
                
                self.bias_angle = math.degrees(math.atan2(avgs[3], avgs[4]))
                self.is_calibrating = False
                print("✅ 보정 완료")
            return

        # 2. 이동 평균 필터 (노이즈 제거)
        self.buf_ax.append(ax)
        self.buf_ay.append(ay)
        self.buf_az.append(az)
        self.buf_gy.append(gy)

        if len(self.buf_ax) >= self.window_size:
            self.f_ax = sum(self.buf_ax) / len(self.buf_ax)
            self.f_ay = sum(self.buf_ay) / len(self.buf_ay)
            self.f_az = sum(self.buf_az) / len(self.buf_az)
            self.f_gy = sum(self.buf_gy) / len(self.buf_gy)

    def update_physics(self):
        if self.is_calibrating: return

        # === 1. 동적 Bias 업데이트 (High-Pass Filter) ===
        # 가속도(A)의 기준점(Bias)이 현재 값(f_a)을 아주 천천히 따라갑니다.
        # 효과: 폰을 기울인 채로 가만히 있으면, 그 상태가 새로운 0점이 됩니다.
        # 0.02는 따라가는 속도 (값이 클수록 빨리 0점으로 돌아옴)
        adaptation_rate = 0.02
        self.running_bias_ax = self.running_bias_ax * (1 - adaptation_rate) + self.f_ax * adaptation_rate
        self.running_bias_ay = self.running_bias_ay * (1 - adaptation_rate) + self.f_ay * adaptation_rate
        self.running_bias_az = self.running_bias_az * (1 - adaptation_rate) + self.f_az * adaptation_rate

        # === 2. 횡이동 (Gyro Y 적분) ===
        # 자이로는 절대 위치가 없으므로 고정 Bias를 사용하되, 데드존을 세게 줍니다.
        gyro_y = self.f_gy - self.bias_gy
        
        # [수정] 데드존 강화: 0.08 미만의 회전은 무시 (멈췄을 때 흐르는 현상 방지)
        if abs(gyro_y) < 0.08: 
            gyro_y = 0.0
            
        self.pos_x += gyro_y * GYRO_SENSITIVITY

        # === 3. 상하 이동 (Accel Y) ===
        # 현재값 - 유동적 Bias (멈추면 0이 됨)
        diff_ay = self.f_ay - self.running_bias_ay
        if abs(diff_ay) < 0.1: diff_ay = 0.0 
        self.pos_y = diff_ay * ACCEL_SENSITIVITY

        # === 4. 화면 회전 (Roll) ===
        current_angle = math.degrees(math.atan2(self.f_ax, self.f_ay))
        
        # 회전 기준 각도도 천천히 현재 각도를 따라가게 할지 결정해야 함.
        # 여기서는 회전은 '절대 수평'을 유지하는 게 좋으므로 고정 Bias 유지 (오뚝이 효과)
        angle_diff = current_angle - self.bias_angle
        if abs(angle_diff) < 1.0: angle_diff = 0.0
        self.rotation_angle = angle_diff * 1.2

        # === 5. [수정] 스케일 (Zoom) ===
        # 현재값 - 유동적 Bias
        # 폰을 떨어뜨려서 Z축 가속도가 변해도, Bias가 따라가므로 diff_az는 곧 0이 됨 -> 크기 원복
        diff_az = self.f_az - self.running_bias_az
        
        # 데드존 적용
        if abs(diff_az) < 0.1: diff_az = 0.0

        # 목표 스케일 계산 (기본 1.0)
        target_scale = 1.0 + (diff_az * ZOOM_SENSITIVITY)
        target_scale = max(0.5, min(3.0, target_scale))
        
        # 부드럽게 돌아가기 (Elasticity)
        self.scale_factor += (target_scale - self.scale_factor) * 0.1

        # === 6. 투명도 ===
        motion = abs(gyro_y) + abs(diff_ay) + abs(diff_az * 2) + abs(angle_diff / 10.0)
        
        if motion > 0.15:
            self.target_opacity = min(1.0, (motion - 0.15) / 0.8) + 0.2
        else:
            self.target_opacity = 0.0
            
        self.current_opacity += (self.target_opacity - self.current_opacity) * 0.1
        self.update()

    def paintEvent(self, event):
        if self.current_opacity < 0.02: return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if self.is_calibrating:
            painter.setPen(QColor(255, 100, 100))
            font = painter.font()
            font.setPointSize(24)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "보정 중...")
            return

        transform = QTransform()
        transform.translate(self.center_x, self.center_y)
        transform.rotate(self.rotation_angle) 
        transform.translate(-self.center_x, -self.center_y)
        painter.setTransform(transform)

        grid_spacing = 130 
        start_x = (self.pos_x % grid_spacing) - grid_spacing
        start_y = (self.pos_y % grid_spacing) - grid_spacing
        margin = int((self.diag_len - min(self.width_limit, self.height_limit)) / 2) + 100
        
        x_start = int(start_x) - margin
        x_end = self.width_limit + margin
        y_start = int(start_y) - margin
        y_end = self.height_limit + margin
        
        safe_zone = min(self.width_limit, self.height_limit) * 0.35
        max_d_sub_safe = (self.diag_len / 2) - safe_zone
        
        brush_cache = {} 

        for x in range(x_start, x_end, grid_spacing):
            for y in range(y_start, y_end, grid_spacing):
                dist = math.hypot(x - self.center_x, y - self.center_y)
                if dist < safe_zone: continue

                ratio = (dist - safe_zone) / max_d_sub_safe
                if ratio > 1.0: ratio = 1.0
                elif ratio < 0.0: ratio = 0.0

                alpha = int(self.current_opacity * ratio * 180)
                if alpha < 10: continue 

                size = (6 + ratio * 8) * self.scale_factor

                if alpha not in brush_cache:
                    brush_cache[alpha] = QBrush(QColor(220, 220, 220, alpha))
                
                painter.setBrush(brush_cache[alpha])
                painter.drawEllipse(QPointF(x - size/2, y - size/2), size, size)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

# ... 트레이 아이콘 부분 (기존과 동일)
def create_tray_icon_pixmap():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(255, 105, 180)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(8, 8, 48, 48)
    painter.end()
    return pixmap

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MotionOverlay()
    window.show()

    tray_icon = QSystemTrayIcon(QIcon(create_tray_icon_pixmap()), app)
    tray_icon.setToolTip("Fixed World Overlay")

    menu = QMenu()
    menu.addAction("0점 재설정", window.start_calibration)
    
    s_menu = menu.addMenu("회전 민감도 설정")
    grp = QActionGroup(app)
    opts = [("느리게 (1.0)", 1.0), ("보통 (4.0)", 4.0), ("빠르게 (8.0)", 8.0)]
    for nm, v in opts:
        act = QAction(nm, app, checkable=True)
        if v == 4.0: act.setChecked(True)
        act.triggered.connect(lambda c, val=v: window.set_gyro_sensitivity(val))
        grp.addAction(act)
        s_menu.addAction(act)

    menu.addSeparator()
    menu.addAction("종료", app.quit)
    tray_icon.setContextMenu(menu)
    tray_icon.show()

    sys.exit(app.exec())