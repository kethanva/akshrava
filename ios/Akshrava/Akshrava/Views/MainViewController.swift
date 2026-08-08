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
    private var isAssistanceRunning = false

    public override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        refreshProvisionStatus()
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

    @objc private func toggleAssistance() {
        if !isAssistanceRunning {
            let provision = ProvisionStore.load()
            if provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                statusLabel.text = "Provisioning required. Set device token before starting."
                AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
                return
            }
            isAssistanceRunning = true
            startButton.setTitle("Stop Assistance", for: .normal)
            startButton.backgroundColor = .systemRed
            startButton.setTitleColor(.white, for: .normal)
            statusLabel.text = "Assistance Running"
            AssistSessionManager.shared.startSession()
        } else {
            isAssistanceRunning = false
            startButton.setTitle("Start Assistance", for: .normal)
            startButton.backgroundColor = .systemYellow
            startButton.setTitleColor(.black, for: .normal)
            statusLabel.text = "Assistance Stopped"
            AssistSessionManager.shared.stopSession()
        }
    }
}
#endif
