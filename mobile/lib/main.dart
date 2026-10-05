import 'package:flutter/material.dart';

import 'src/controller.dart';
import 'src/settings.dart';
import 'src/ui/home_screen.dart';

/// Buddy for Android: talks to the same backend as the desktop app, over Wi-Fi.
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = await BuddySettings.load();
  final controller = BuddyController(settings);
  await controller.init();
  runApp(BuddyApp(controller: controller));
}

class BuddyApp extends StatelessWidget {
  const BuddyApp({super.key, required this.controller});

  final BuddyController controller;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Buddy',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, brightness: Brightness.dark, colorSchemeSeed: const Color(0xFF4A7DFF)),
      home: HomeScreen(controller: controller),
    );
  }
}
