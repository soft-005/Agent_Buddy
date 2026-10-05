import 'dart:async';

import 'package:flutter/material.dart';

import '../controller.dart';

/// Startup progress: connecting, speech recognition, language model.
class LoaderCard extends StatefulWidget {
  const LoaderCard({super.key, required this.controller});

  final BuddyController controller;

  @override
  State<LoaderCard> createState() => _LoaderCardState();
}

class _LoaderCardState extends State<LoaderCard> {
  late final Timer _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(milliseconds: 500), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _tick.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final seconds = DateTime.now().difference(c.loaderStarted).inSeconds;
    return AnimatedOpacity(
      opacity: c.loaderReady ? 0.85 : 1,
      duration: const Duration(milliseconds: 300),
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
        decoration: BoxDecoration(
          color: const Color(0xE0181A22),
          borderRadius: BorderRadius.circular(16),
          boxShadow: const [BoxShadow(color: Color(0x55000000), blurRadius: 18, offset: Offset(0, 4))],
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                c.loaderReady
                    ? const Icon(Icons.check_circle, color: Color(0xFF30A46C), size: 18)
                    : const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(c.loaderTitle, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15)),
                ),
                if (!c.loaderReady) Text('$seconds s', style: const TextStyle(color: Color(0xFF9AA3B5))),
              ],
            ),
            const SizedBox(height: 8),
            for (final step in [c.backendStep, c.sttStep, c.llmStep]) _StepRow(step),
            if (c.loaderHint.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(c.loaderHint, style: const TextStyle(color: Color(0xFFC8CEDE), fontSize: 13)),
              ),
          ],
        ),
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow(this.step);

  final LoaderStep step;

  @override
  Widget build(BuildContext context) {
    final (Widget icon, Color color) = switch (step.state) {
      LoaderStepState.done => (const Icon(Icons.circle, size: 11, color: Color(0xFF30A46C)), const Color(0xFFB9C2D3)),
      LoaderStepState.error => (const Icon(Icons.circle, size: 11, color: Color(0xFFE5484D)), const Color(0xFFFFB3B5)),
      LoaderStepState.active => (
        const SizedBox(width: 11, height: 11, child: CircularProgressIndicator(strokeWidth: 1.8)),
        const Color(0xFFEEF1F7),
      ),
      LoaderStepState.waiting => (
        const Icon(Icons.circle_outlined, size: 11, color: Color(0xFF4A5163)),
        const Color(0xFF8A93A6),
      ),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          SizedBox(width: 14, child: Center(child: icon)),
          const SizedBox(width: 10),
          Expanded(
            child: Text(step.label, style: TextStyle(color: color, fontSize: 13.5)),
          ),
        ],
      ),
    );
  }
}
