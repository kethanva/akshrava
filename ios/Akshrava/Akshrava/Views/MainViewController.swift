//
//  MainViewController.swift
//  Akshrava iOS
//

import Foundation
#if os(iOS)
import UIKit

public class MainViewController: UIViewController {
    private let startButton = UIButton(type: .system)
    private let statusLabel = UILabel()

    public override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        refreshProvisionStatus()
        refreshControlState()
    }

    private func setupUI() {
        view.backgroundColor = .black

        statusLabel.textColor = .white
        statusLabel.font = UIFont.preferredFont(forTextStyle: .title2)
        statusLabel.textAlignment = .center
        statusLabel.numberOfLines = 0
        statusLabel.translatesAutoresizingMaskIntoConstraints = false
        statusLabel.accessibilityTraits = .updatesFrequently

        startButton.setTitle("Start Assistance", for: .normal)
        startButton.titleLabel?.font = UIFont.boldSystemFont(ofSize: 24)
        startButton.setTitleColor(.black, for: .normal)
        startButton.backgroundColor = .systemYellow
        startButton.layer.cornerRadius = 16
        startButton.translatesAutoresizingMaskIntoConstraints = false
        startButton.accessibilityLabel = "Start or stop vision assistance"
        startButton.addTarget(self, action: #selector(toggleAssistance), for: .touchUpInside)

        view.addSubview(statusLabel)
        view.addSubview(startButton)

        NSLayoutConstraint.activate([
            statusLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            statusLabel.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 40),
            statusLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 20),
            statusLabel.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -20),

            startButton.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            startButton.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            startButton.widthAnchor.constraint(equalToConstant: 280),
            startButton.heightAnchor.constraint(equalToConstant: 80),
        ])
    }

    private func refreshProvisionStatus() {
        let provision = ProvisionStore.load()
        if provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            statusLabel.text = "Provisioning required. Set device token before starting."
            startButton.isEnabled = true
        } else {
            statusLabel.text = "Akshrava Assistive Vision"
        }
    }

    /// Reads AssistSessionManager.shared.isSessionActive rather than mirroring it in a local
    /// bool. A local `isAssistanceRunning` could desync from the real session -- a terminal
    /// disconnect (auth revoked, camera permanently unavailable) sets the manager inactive on its
    /// own, and a UI that did not observe that still showed "Stop Assistance". The next tap then
    /// called stopSession() on an already-inactive session (a silent no-op) instead of Start,
    /// leaving a blind user's only recovery path requiring an unexplained second tap.
    @objc private func toggleAssistance() {
        if !AssistSessionManager.shared.isSessionActive {
            let provision = ProvisionStore.load()
            if provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                statusLabel.text = "Provisioning required. Set device token before starting."
                AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
                return
            }
            AssistSessionManager.shared.startSession()
        } else {
            AssistSessionManager.shared.stopSession()
            AlertManager.shared.speakStatus("Assistance stopped.", force: true)
        }
        refreshControlState()
    }

    private func refreshControlState() {
        let running = AssistSessionManager.shared.isSessionActive
        startButton.setTitle(running ? "Stop Assistance" : "Start Assistance", for: .normal)
        startButton.backgroundColor = running ? .systemRed : .systemYellow
        startButton.setTitleColor(running ? .white : .black, for: .normal)
        statusLabel.text = running ? "Assistance Running" : "Assistance Stopped"
    }
}
#endif
