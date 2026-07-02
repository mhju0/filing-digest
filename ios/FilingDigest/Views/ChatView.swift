//
//  ChatView.swift
//  FilingDigest
//
//  Chat tab: message list + input bar + send. Assistant messages show their
//  citations (title + source) underneath — every claim carries a citation.
//

import SwiftUI

/// Local-only chat transcript entry (not part of the API contract).
struct ChatMessage: Identifiable, Equatable {
    enum Role {
        case user
        case assistant
    }

    let id = UUID()
    let role: Role
    let text: String
    var citations: [Citation] = []
    var isError = false
}

struct ChatView: View {
    let client: APIClient

    @State private var messages: [ChatMessage] = []
    @State private var input = ""
    @State private var isSending = false
    @State private var language: Language = .ko

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                messageList
                Divider()
                inputBar
            }
            .navigationTitle("챗")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Picker("언어", selection: $language) {
                        Text("KO").tag(Language.ko)
                        Text("EN").tag(Language.en)
                    }
                    .pickerStyle(.segmented)
                }
            }
        }
    }

    // MARK: Message list

    @ViewBuilder
    private var messageList: some View {
        if messages.isEmpty {
            ContentUnavailableView(
                "공시에 대해 질문하세요",
                systemImage: "bubble.left.and.text.bubble.right",
                description: Text("예: \"삼성전자 최근 분기 매출은?\" 답변에는 항상 인용이 붙습니다.")
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(messages) { message in
                            ChatBubble(message: message)
                                .id(message.id)
                        }
                        if isSending {
                            HStack {
                                ProgressView()
                                Text("답변 생성 중…")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    .padding()
                }
                .onChange(of: messages.count) { _, _ in
                    if let last = messages.last {
                        withAnimation {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
            }
        }
    }

    // MARK: Input bar

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("질문을 입력하세요", text: $input, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.roundedBorder)
                .onSubmit {
                    Task { await send() }
                }
            Button {
                Task { await send() }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title2)
            }
            .disabled(!canSend)
            .accessibilityLabel("전송")
        }
        .padding()
    }

    private var canSend: Bool {
        !isSending && !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: Actions

    private func send() async {
        let question = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !isSending else { return }

        input = ""
        messages.append(ChatMessage(role: .user, text: question))
        isSending = true
        defer { isSending = false }

        do {
            // company_id: nil — the chat tab has no company context; digest
            // screens could pass one in a future iteration.
            let response = try await client.sendChat(
                ChatRequest(companyId: nil, question: question, language: language)
            )
            messages.append(
                ChatMessage(role: .assistant, text: response.answer, citations: response.citations)
            )
        } catch {
            messages.append(
                ChatMessage(role: .assistant, text: error.localizedDescription, isError: true)
            )
        }
    }
}

// MARK: - Bubble

private struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.role == .user {
                Spacer(minLength: 40)
            }
            VStack(alignment: .leading, spacing: 8) {
                Text(message.text)
                    .foregroundStyle(foregroundColor)
                if !message.citations.isEmpty {
                    Divider()
                    ForEach(message.citations) { citation in
                        CitationRow(citation: citation)
                    }
                }
            }
            .padding(12)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(backgroundColor)
            )
            if message.role == .assistant {
                Spacer(minLength: 40)
            }
        }
    }

    private var backgroundColor: Color {
        switch message.role {
        case .user:
            return Color.accentColor
        case .assistant:
            return message.isError ? Color.red.opacity(0.12) : Color(.secondarySystemBackground)
        }
    }

    private var foregroundColor: Color {
        message.role == .user ? Color.white : Color.primary
    }
}
