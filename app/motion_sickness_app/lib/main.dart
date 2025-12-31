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

Future<void> initializeService() async {
  final service = FlutterBackgroundService();
  const AndroidNotificationChannel channel = AndroidNotificationChannel(
    'sensor_service_channel',
    'Sensor Service',
    description: 'Sending Full Motion Data',
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
      autoStart: false,
      isForegroundMode: true,
      notificationChannelId: 'sensor_service_channel',
      initialNotificationTitle: '모션 데이터 전송 중',
      initialNotificationContent: '3축 자이로 + Z축 가속도 전송',
      foregroundServiceNotificationId: 888,
    ),
    iosConfiguration: IosConfiguration(),
  );
}

@pragma('vm:entry-point')
void onStart(ServiceInstance service) async {
  DartPluginRegistrant.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  final ip = prefs.getString('target_ip') ?? '192.168.0.1';
  final port = 8989;

  RawDatagramSocket? socket;
  try {
    socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
  } catch (e) {
    service.stopSelf();
    return;
  }

  // 데이터 저장 변수 (6개 축)
  double gX = 0.0, gY = 0.0, gZ = 0.0;
  double aX = 0.0, aY = 0.0, aZ = 0.0;

  // 1. 가속도 센서 구독 (UserAccelerometer: 중력 제외)
  // 만약 폰을 기울였을 때 중력 값을 쓰고 싶다면 userAccelerometerEventStream 대신 accelerometerEventStream 사용
  StreamSubscription? accelSub =
      accelerometerEventStream(
        samplingPeriod: SensorInterval.gameInterval,
      ).listen((event) {
        aX = event.x; // 여기가 0이었던 원인! 이제 값을 채워줍니다.
        aY = event.y;
        aZ = event.z;
      });

  // 2. 자이로스코프 구독
  StreamSubscription? gyroSub =
      gyroscopeEventStream(samplingPeriod: SensorInterval.gameInterval).listen((
        event,
      ) {
        gX = event.x;
        gY = event.y;
        gZ = event.z;
      });

  // 3. 데이터 전송 (20ms 간격)
  Timer? sendTimer = Timer.periodic(const Duration(milliseconds: 20), (timer) {
    if (socket == null) return;

    // [중요] 6개 데이터를 모두 JSON에 담습니다.
    final data = jsonEncode({
      "gx": double.parse(gX.toStringAsFixed(4)),
      "gy": double.parse(gY.toStringAsFixed(4)),
      "gz": double.parse(gZ.toStringAsFixed(4)),
      "ax": double.parse(aX.toStringAsFixed(4)), // PC에서 기다리는 값
      "ay": double.parse(aY.toStringAsFixed(4)), // PC에서 기다리는 값
      "az": double.parse(aZ.toStringAsFixed(4)),
    });

    try {
      socket!.send(utf8.encode(data), InternetAddress(ip), port);
    } catch (e) {
      // 에러 무시
    }
  });

  service.on('stopService').listen((event) {
    accelSub?.cancel();
    gyroSub?.cancel();
    sendTimer?.cancel();
    socket?.close();
    service.stopSelf();
  });
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData.dark(),
      home: const SensorControlPage(),
    );
  }
}

class SensorControlPage extends StatefulWidget {
  const SensorControlPage({super.key});
  @override
  State<SensorControlPage> createState() => _SensorControlPageState();
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
      await prefs.setString('target_ip', _ipController.text);
      await service.startService();
      setState(() => _isRunning = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text("Vertical Motion Sender")),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(
              Icons.screen_lock_portrait,
              size: 80,
              color: Colors.purpleAccent,
            ),
            const SizedBox(height: 20),
            TextField(
              controller: _ipController,
              decoration: const InputDecoration(
                labelText: "PC IP Address",
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.computer),
              ),
              keyboardType: TextInputType.number,
            ),
            const SizedBox(height: 40),
            SizedBox(
              width: double.infinity,
              height: 60,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: _isRunning
                      ? Colors.redAccent
                      : Colors.purple,
                  foregroundColor: Colors.white,
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
              "휴대전화를 세로로 세워서\n거치대에 두고 사용하세요.",
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
          ],
        ),
      ),
    );
  }
}
