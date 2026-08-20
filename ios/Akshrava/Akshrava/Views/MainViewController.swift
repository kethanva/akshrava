//
//  MainViewController.swift
//  Akshrava iOS
//

import Foundation
#if os(iOS)
import UIKit

public class MainViewController: UIViewController {
    private let scrollView = UIScrollView()
    private let contentStack = UIStackView()
    private let startButton = UIButton(type: .system)
    private let statusLabel = UILabel()
    private let setupButton = UIButton(type: .system)
    private let setupPanel = UIStackView()
    private let endpointField = UITextField()
    private let tokenField = UITextField()
    private let calibrationField = UITextField()
    private let languageButton = UIButton(type: .system)
    private let saveSetupButton = UIButton(type: .system)

    private var setupExpanded = false
    private var selectedLanguageTag = "en-IN"

    public override func viewDidLoad() {
        super.viewDidLoad()
        setupUI()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(sessionStateDidChange(_:)),
            name: AssistSessionManager.sessionStateDidChangeNotification,
            object: nil
        )
        loadProvisionIntoForm()
        let provisioned = provisioningIsReady(ProvisionStore.load())
        setSetupExpanded(!provisioned)
        refreshProvisionStatus()
        refreshControlState()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func sessionStateDidChange(_ notification: Notification) {
        refreshControlState()
    }

    private func setupUI() {
        view.backgroundColor = .black

        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.alwaysBounceVertical = true
        scrollView.keyboardDismissMode = .interactive

        contentStack.axis = .vertical
        contentStack.alignment = .fill
        contentStack.spacing = 16
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.isLayoutMarginsRelativeArrangement = true
        contentStack.layoutMargins = UIEdgeInsets(top: 32, left: 24, bottom: 40, right: 24)

        statusLabel.textColor = .white
        statusLabel.font = UIFont.preferredFont(forTextStyle: .title2)
        statusLabel.adjustsFontForContentSizeCategory = true
        statusLabel.textAlignment = .center
        statusLabel.numberOfLines = 0
        statusLabel.accessibilityTraits = .updatesFrequently

        startButton.setTitle("Start Assistance", for: .normal)
        startButton.titleLabel?.font = UIFont.preferredFont(forTextStyle: .title2)
        startButton.titleLabel?.adjustsFontForContentSizeCategory = true
        startButton.setTitleColor(.black, for: .normal)
        startButton.backgroundColor = .systemYellow
        startButton.layer.cornerRadius = 16
        startButton.accessibilityLabel = "Start or stop vision assistance"
        startButton.addTarget(self, action: #selector(toggleAssistance), for: .touchUpInside)
        startButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 80).isActive = true

        setupButton.setTitle("Show Setup", for: .normal)
        setupButton.contentHorizontalAlignment = .left
        setupButton.titleLabel?.font = UIFont.preferredFont(forTextStyle: .headline)
        setupButton.titleLabel?.adjustsFontForContentSizeCategory = true
        setupButton.addTarget(self, action: #selector(toggleSetup), for: .touchUpInside)
        setupButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true

        setupPanel.axis = .vertical
        setupPanel.alignment = .fill
        setupPanel.spacing = 12
        setupPanel.isLayoutMarginsRelativeArrangement = true
        setupPanel.layoutMargins = UIEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        setupPanel.backgroundColor = UIColor(white: 0.12, alpha: 1)
        setupPanel.layer.cornerRadius = 12

        let helpLabel = UILabel()
        helpLabel.text = "Enter values supplied by an authorized provisioning workstation. A blank token keeps the credential already stored on this phone."
        helpLabel.textColor = .lightGray
        helpLabel.font = UIFont.preferredFont(forTextStyle: .body)
        helpLabel.adjustsFontForContentSizeCategory = true
        helpLabel.numberOfLines = 0

        configureTextField(endpointField, placeholder: "WSS endpoint")
        endpointField.keyboardType = .URL
        configureTextField(tokenField, placeholder: "Device token")
        tokenField.isSecureTextEntry = true
        configureTextField(calibrationField, placeholder: "Camera calibration ID")

        languageButton.contentHorizontalAlignment = .left
        languageButton.backgroundColor = UIColor(white: 0.2, alpha: 1)
        languageButton.layer.cornerRadius = 8
        languageButton.accessibilityLabel = "Spoken language"
        languageButton.showsMenuAsPrimaryAction = true
        languageButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true

        saveSetupButton.setTitle("Save Setup", for: .normal)
        saveSetupButton.titleLabel?.font = UIFont.preferredFont(forTextStyle: .headline)
        saveSetupButton.titleLabel?.adjustsFontForContentSizeCategory = true
        saveSetupButton.backgroundColor = .systemBlue
        saveSetupButton.setTitleColor(.white, for: .normal)
        saveSetupButton.layer.cornerRadius = 10
        saveSetupButton.addTarget(self, action: #selector(saveSetup), for: .touchUpInside)
        saveSetupButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 52).isActive = true

        [helpLabel, endpointField, tokenField, calibrationField, languageButton, saveSetupButton]
            .forEach(setupPanel.addArrangedSubview)
        [statusLabel, startButton, setupButton, setupPanel].forEach(contentStack.addArrangedSubview)
        view.addSubview(scrollView)
        scrollView.addSubview(contentStack)

        NSLayoutConstraint.activate([
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.topAnchor.constraint(equalTo: view.topAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            contentStack.leadingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.leadingAnchor),
            contentStack.trailingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.trailingAnchor),
            contentStack.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor),
            contentStack.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor),
            contentStack.widthAnchor.constraint(equalTo: scrollView.frameLayoutGuide.widthAnchor),
        ])
    }

    private func configureTextField(_ field: UITextField, placeholder: String) {
        field.placeholder = placeholder
        field.textColor = .white
        field.backgroundColor = UIColor(white: 0.2, alpha: 1)
        field.layer.cornerRadius = 8
        field.autocapitalizationType = .none
        field.autocorrectionType = .no
        field.spellCheckingType = .no
        field.textContentType = nil
        field.clearButtonMode = .whileEditing
        field.font = UIFont.preferredFont(forTextStyle: .body)
        field.adjustsFontForContentSizeCategory = true
        field.borderStyle = .roundedRect
        field.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true
    }

    private func loadProvisionIntoForm() {
        let provision = ProvisionStore.load()
        endpointField.text = provision.endpoint
        calibrationField.text = provision.calibrationId
        tokenField.text = ""
        tokenField.placeholder = provision.deviceToken.isEmpty ? "Device token" : "Device token stored"
        selectedLanguageTag = canonicalLanguageTag(provision.language)
        rebuildLanguageMenu()
    }

    private func canonicalLanguageTag(_ language: String) -> String {
        let normalized = language.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return SupportedLanguages.all.first {
            normalized == $0.tag.lowercased() || normalized == $0.wireCode
        }?.tag ?? "en-IN"
    }

    private func rebuildLanguageMenu() {
        let selected = SupportedLanguages.all.first { $0.tag == selectedLanguageTag }
            ?? SupportedLanguages.all[0]
        languageButton.setTitle("Language: \(selected.label)", for: .normal)
        languageButton.menu = UIMenu(children: SupportedLanguages.all.map { language in
            UIAction(
                title: language.label,
                state: language.tag == selectedLanguageTag ? .on : .off
            ) { [weak self] _ in
                self?.selectedLanguageTag = language.tag
                self?.rebuildLanguageMenu()
            }
        })
    }

    @objc private func toggleSetup() {
        setSetupExpanded(!setupExpanded)
    }

    private func setSetupExpanded(_ expanded: Bool) {
        setupExpanded = expanded
        setupPanel.isHidden = !expanded
        let title = expanded ? "Hide Setup" : "Show Setup"
        setupButton.setTitle(title, for: .normal)
        setupButton.accessibilityLabel = title
    }

    private func candidateProvision() -> DeviceProvision {
        let previous = ProvisionStore.load()
        let typedToken = tokenField.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return DeviceProvision(
            endpoint: endpointField.text ?? "",
            deviceToken: typedToken.isEmpty ? previous.deviceToken : typedToken,
            language: selectedLanguageTag,
            calibrationId: calibrationField.text ?? ""
        )
    }

    @discardableResult
    private func persistProvisionFromForm(announceSuccess: Bool) -> Bool {
        let candidate = candidateProvision()
        guard candidate.isReady else {
            setSetupExpanded(true)
            statusLabel.text = "Provisioning is incomplete. Check the WSS endpoint, device token, calibration ID, and language."
            AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
            AgentDebugLog.error(event: "provision_save_rejected", detail: "reason=incomplete_fields")
            return false
        }
        guard ProvisionStore.save(candidate) else {
            tokenField.text = ""
            setSetupExpanded(true)
            statusLabel.text = "Secure token storage failed. Re-enter setup before starting."
            AlertManager.shared.speakStatus("Device setup could not be saved.", force: true)
            return false
        }
        tokenField.text = ""
        tokenField.placeholder = "Device token stored"
        if announceSuccess {
            statusLabel.text = "Setup saved."
            AlertManager.shared.speakStatus("Setup saved.", language: candidate.language, force: true)
        }
        setSetupExpanded(false)
        return true
    }

    @objc private func saveSetup() {
        view.endEditing(true)
        _ = persistProvisionFromForm(announceSuccess: true)
    }

    private func refreshProvisionStatus() {
        let provision = ProvisionStore.load()
        if !provisioningIsReady(provision) {
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
            let current = ProvisionStore.load()
            if setupExpanded || !provisioningIsReady(current) {
                guard persistProvisionFromForm(announceSuccess: false) else { return }
            }
            guard provisioningIsReady(ProvisionStore.load()) else {
                setSetupExpanded(true)
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
        setupButton.isEnabled = !running
        setupPanel.isUserInteractionEnabled = !running
        if running {
            statusLabel.text = "Assistance Running"
        } else if !provisioningIsReady(ProvisionStore.load()) {
            statusLabel.text = "Provisioning required. Set device token before starting."
        } else {
            statusLabel.text = "Assistance Stopped"
        }
    }

    private func provisioningIsReady(_ provision: DeviceProvision) -> Bool {
        provision.isReady
    }
}
#endif
