import 'dart:async';

import 'package:flutter/material.dart';

import '../controller.dart';

/// Buddy asks before an action set to "ask" in backend/tools.json on the PC.
class ApprovalCard extends StatefulWidget {
  const ApprovalCard({super.key, required this.controller, required this.approval});

  final BuddyController controller;
  final PendingApproval approval;

  @override
  State<ApprovalCard> createState() => _ApprovalCardState();
}

class _ApprovalCardState extends State<ApprovalCard> {
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
    final a = widget.approval;
    final seconds = a.deadline.difference(DateTime.now()).inSeconds.clamp(0, 999);
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      decoration: BoxDecoration(
        color: const Color(0xF2181A22),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0x8CF5A524)),
        boxShadow: const [BoxShadow(color: Color(0x66000000), blurRadius: 18, offset: Offset(0, 4))],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              const Text(
                'BUDDY WANTS TO',
                style: TextStyle(
                  color: Color(0xFFF5A524),
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.6,
                ),
              ),
              const Spacer(),
              Text('$seconds s', style: const TextStyle(color: Color(0xFF9AA3B5), fontSize: 12)),
            ],
          ),
          const SizedBox(height: 4),
          Text(a.title, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
          if (a.detail.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(top: 8),
              padding: const EdgeInsets.all(8),
              constraints: const BoxConstraints(maxHeight: 120),
              width: double.infinity,
              decoration: BoxDecoration(color: const Color(0x14FFFFFF), borderRadius: BorderRadius.circular(8)),
              child: SingleChildScrollView(
                child: SelectableText(
                  a.detail,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: Color(0xFFC8CEDE)),
                ),
              ),
            ),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              OutlinedButton(onPressed: () => widget.controller.answerApproval(false), child: const Text('Deny')),
              const SizedBox(width: 10),
              FilledButton(
                style: FilledButton.styleFrom(backgroundColor: const Color(0xFF30A46C)),
                onPressed: () => widget.controller.answerApproval(true),
                child: const Text('Allow'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
