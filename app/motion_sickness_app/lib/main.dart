import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_background_service/flutter_background_service.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:sensors_plus/sensors_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await initializeService();
  runApp(const MyApp());
}

// --- 백그라운드 서비스 초기화 로직 ---
Future<void> initializeService() async {
  final service = FlutterBackgroundService();

  const AndroidNotificationChannel channel = AndroidNotificationChannel(
    'sensor_service_channel',
    'Sensor Service',
    description: 'Sending sensor data to PC',
    importance: Importance.low,
  );

  final FlutterLocalNotificationsPlugin flutterLocalNotificationsPlugin =
      FlutterLocalNotificationsPlugin();

  await flutterLocalNotificationsPlugin
      .resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >()
      ?.createNotificationChannel(channel);

  await service.configure(
    androidConfiguration: AndroidConfiguration(
      onStart: onStart,
      autoStart: false, // 앱 켜자마자 시작하지 않음 (버튼 눌러야 시작)
      isForegroundMode: true,
      notificationChannelId: 'sensor_service_channel',
      initialNotificationTitle: '센서 전송 중',
      initialNotificationContent: '백그라운드에서 실행 중입니다.',
      foregroundServiceNotificationId: 888,
    ),
    iosConfiguration: IosConfiguration(), // iOS는 일단 기본 설정
  );
}

// --- 실제 백그라운드에서 돌아가는 함수 (여기가 핵심) ---
@pragma('vm:entry-point')
void onStart(ServiceInstance service) async {
  DartPluginRegistrant.ensureInitialized();

  // 저장된 IP/Port 가져오기
  final prefs = await SharedPreferences.getInstance();
  final ip = prefs.getString('target_ip') ?? '192.168.0.1';
  final port = prefs.getInt('target_port') ?? 8989;

  // UDP 소켓 생성
  RawDatagramSocket? socket;
  try {
    socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
  } catch (e) {
    print("Socket Error: $e");
    service.stopSelf();
    return;
  }

  // 가속도계 구독 (UserAccelerometer: 중력 제외한 순수 가속도)
  StreamSubscription? subscription;
  subscription =
      userAccelerometerEventStream(
            samplingPeriod: SensorInterval.gameInterval,
          ) // 빠른 속도
          .listen((UserAccelerometerEvent event) {
            if (socket == null) return;

            // 데이터 포맷: JSON {"x": 1.2, "y": -0.5}
            // 데이터 양을 줄이기 위해 소수점 4자리까지만 자름
            final data = jsonEncode({
              "x": double.parse(event.x.toStringAsFixed(4)),
              "y": double.parse(event.y.toStringAsFixed(4)),
            });

            socket.send(utf8.encode(data), InternetAddress(ip), port);
          });

  // 서비스 종료 신호 받으면 정리
  service.on('stopService').listen((event) {
    subscription?.cancel();
    socket?.close();
    service.stopSelf();
  });
}

// --- UI 코드 ---
class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(home: SensorControlPage(), theme: ThemeData.dark());
  }
}

class SensorControlPage extends StatefulWidget {
  const SensorControlPage({super.key});

  @override
  _SensorControlPageState createState() => _SensorControlPageState();
}

class _SensorControlPageState extends State<SensorControlPage> {
  final TextEditingController _ipController = TextEditingController();
  bool _isRunning = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
    _checkServiceStatus();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _ipController.text = prefs.getString('target_ip') ?? '';
    });
  }

  Future<void> _checkServiceStatus() async {
    final isRunning = await FlutterBackgroundService().isRunning();
    setState(() {
      _isRunning = isRunning;
    });
  }

  Future<void> _toggleService() async {
    final service = FlutterBackgroundService();
    final prefs = await SharedPreferences.getInstance();

    if (_isRunning) {
      service.invoke('stopService');
      setState(() => _isRunning = false);
    } else {
      if (_ipController.text.isEmpty) return;

      // 시작 전 IP 저장
      await prefs.setString('target_ip', _ipController.text);
      await prefs.setInt('target_port', 8989); // 포트는 8989 고정 (필요시 수정)

      await service.startService();
      setState(() => _isRunning = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Motion Sickness Sender")),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TextField(
              controller: _ipController,
              decoration: const InputDecoration(
                labelText: "PC IP Address",
                border: OutlineInputBorder(),
                hintText: "192.168.0.xxx",
              ),
              keyboardType: TextInputType.number,
            ),
            const SizedBox(height: 40),
            SizedBox(
              width: double.infinity,
              height: 60,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: _isRunning ? Colors.red : Colors.green,
                ),
                onPressed: _toggleService,
                child: Text(
                  _isRunning ? "STOP SENDING" : "START SENDING",
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 20),
            const Text(
              "Start를 누르고 화면을 꺼도\n데이터는 계속 전송됩니다.",
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }
}
