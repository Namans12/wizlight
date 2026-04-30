import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:wizlight_android/models/wiz_bulb.dart';
import 'package:wizlight_android/services/sync_service.dart';

void main() {
  test('WizBulb JSON round-trip keeps IP, name, mac, and region', () {
    const bulb = WizBulb(
      ip: '192.168.1.4',
      name: 'Living Room',
      mac: 'CC4085E2D228',
      region: 'right',
    );

    final restored = WizBulb.fromJson(bulb.toJson());

    expect(restored.ip, bulb.ip);
    expect(restored.name, bulb.name);
    expect(restored.mac, bulb.mac);
    expect(restored.region, bulb.region);
  });

  test('SyncRuntimeStats primary color averages per-bulb output', () {
    const runtime = SyncRuntimeStats(
      mode: 'zones',
      currentFps: 24,
      sendRateHz: 12.0,
      motionScore: 0.02,
      smoothing: 0.2,
      updatesSent: 3,
      outputColors: <String, Color>{
        '192.168.1.4': Color.fromARGB(255, 200, 100, 0),
        '192.168.1.5': Color.fromARGB(255, 0, 100, 200),
      },
    );

    final average = runtime.primaryOutputColor!;

    expect((average.r * 255.0).round(), 100);
    expect((average.g * 255.0).round(), 100);
    expect((average.b * 255.0).round(), 100);
  });
}
