//
//  InterfaceLanguageTests.swift
//  FilingDigestTests
//
//  The digest's KO/EN toggle is the app's one language control (D19). These
//  cover the copy it switches on the digest, answer and evidence screens:
//  Korean output stays exactly as before, English output carries no Hangul
//  of its own.
//

import Foundation
import Testing
@testable import FilingDigest

private func containsHangul(_ text: String) -> Bool {
    text.unicodeScalars.contains { (0xAC00...0xD7A3).contains($0.value) }
}

@Suite("Interface language")
struct InterfaceLanguageTests {
    private let samsung = Company(
        id: UUID().uuidString,
        name: "삼성전자",
        nameEn: "Samsung Electronics",
        ticker: "005930",
        market: .kospi,
        source: .dart
    )
    private let apple = Company(
        id: UUID().uuidString,
        name: "Apple Inc.",
        nameEn: "Apple Inc.",
        ticker: "AAPL",
        market: .nasdaq,
        source: .sec
    )

    @Test("Company header follows the selected language")
    func companyHeader() {
        #expect(samsung.displayName(.en) == "Samsung Electronics")
        #expect(samsung.securityIdentifier(.en) == "Stock code 005930")
        #expect(apple.securityIdentifier(.en) == "Ticker AAPL")
        #expect(Market.kospi.displayName(.en) == "KOSPI")
        #expect(Market.nyse.displayName(.en) == "NYSE")

        #expect(samsung.displayName(.ko) == samsung.koreanDisplayName)
        #expect(apple.displayName(.ko) == "애플")
        #expect(samsung.securityIdentifier(.ko) == "종목코드 005930")
        #expect(Market.nyse.displayName(.ko) == "뉴욕증권거래소")
    }

    @Test("A company without an English name keeps its disclosed name")
    func englishNameFallback() {
        let company = Company(
            id: UUID().uuidString,
            name: "예시홀딩스",
            nameEn: nil,
            ticker: "123456",
            market: .kosdaq,
            source: .dart
        )
        #expect(company.displayName(.en) == "예시홀딩스")
    }

    @Test("Regulator legal names give way to familiar English names")
    func familiarEnglishNames() {
        let regulatorNamed = Company(
            id: UUID().uuidString,
            name: "삼성전자",
            nameEn: "SAMSUNG ELECTRONICS CO,.LTD",
            ticker: "005930",
            market: .kospi,
            source: .dart
        )
        #expect(regulatorNamed.displayName(.en) == "Samsung Electronics")
        #expect(apple.displayName(.en) == "Apple")
        let unknown = Company(
            id: UUID().uuidString,
            name: "Example Holdings Inc.",
            nameEn: "EXAMPLE HOLDINGS INC",
            ticker: "EXM",
            market: .nyse,
            source: .sec
        )
        #expect(unknown.displayName(.en) == "EXAMPLE HOLDINGS INC")
    }

    @Test("Withheld-narrative messages exist in both languages")
    func blockedReasonMessages() {
        for reason in NarrativeBlockedReason.allCases {
            #expect(reason.userMessage(.ko) == reason.userMessage)
            #expect(!containsHangul(reason.userMessage(.en)))
        }
        #expect(!containsHangul(AnswerCopy.blockedFallback(.en)))
    }

    @Test("Evidence location reads as section, part and paragraph")
    func anchorText() {
        #expect(
            AnswerCopy.anchor(sectionOrder: 2, partIndex: 1, chunkIndex: 4, language: .ko)
                == "원문 구역 2 · 부분 1 · 문단 4"
        )
        #expect(
            AnswerCopy.anchor(sectionOrder: 2, partIndex: 1, chunkIndex: 4, language: .en)
                == "Section 2 · Part 1 · Paragraph 4"
        )
        #expect(
            AnswerCopy.anchor(sectionOrder: nil, partIndex: nil, chunkIndex: 7, language: .en)
                == "Paragraph 7"
        )
    }

    @Test("Figure period line keeps Korean wording and gains an English one")
    func figurePeriod() {
        #expect(
            AnswerCopy.figurePeriod(
                title: "사업보고서 2025", isInstant: false, fiscalYear: 2025, quarter: nil, language: .ko
            ) == "사업보고서 2025 · 기간 · 회계연도 2025"
        )
        #expect(
            AnswerCopy.figurePeriod(
                title: "2026년 1분기", isInstant: true, fiscalYear: 2026, quarter: 1, language: .ko
            ) == "2026년 1분기 · 기준일 · 회계연도 2026 · 1분기"
        )
        #expect(
            AnswerCopy.figurePeriod(
                title: "Q1 2026", isInstant: true, fiscalYear: 2026, quarter: 1, language: .en
            ) == "Q1 2026 · As of · FY2026 · Q1"
        )
    }

    @Test("Answer screen copy has no Korean in English")
    func answerCopy() {
        let english = [
            AnswerCopy.title(companyName: "Samsung Electronics", language: .en),
            AnswerCopy.inputPlaceholder(hasResponse: false, language: .en),
            AnswerCopy.inputPlaceholder(hasResponse: true, language: .en),
            AnswerCopy.starterTitle(.en),
            AnswerCopy.starterBody(.en),
            AnswerCopy.suggestionsTitle(.en),
            AnswerCopy.claims(3, language: .en),
            AnswerCopy.evidenceVerified(.en),
            AnswerCopy.noResultsTitle(.en),
            AnswerCopy.noResultsBody(.en),
            AnswerCopy.figuresTitle(.en),
            AnswerCopy.evidenceNumber(1, language: .en),
            AnswerCopy.evidenceTitle(.en),
            AnswerCopy.openFiling(.en),
        ] + AnswerCopy.suggestedQuestions(.en)
        for text in english {
            #expect(!containsHangul(text), "\(text)")
        }
        #expect(AnswerCopy.claims(1, language: .en) == "Answer / 1 claim")
        #expect(AnswerCopy.claims(3, language: .ko) == "답변 / 주장 3개")
        #expect(AnswerCopy.evidenceNumber(1, language: .ko) == "근거 01")
        #expect(AnswerCopy.suggestedQuestions(.ko) == [
            "주요 사업 부문은 무엇인가요",
            "주요 리스크 요인은 무엇인가요",
            "연구개발 조직은 어떻게 구성되어 있나요",
        ])
    }
}
