import XCTest

final class FilingDigestUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testCompanyDigestAndCitedAnswerFlow() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()

        let company = app.buttons["company-005930"].firstMatch
        XCTAssertTrue(company.waitForExistence(timeout: 5))
        company.tap()

        XCTAssertTrue(app.descendants(matching: .any)["digest-screen"].waitForExistence(timeout: 5))
        app.buttons["ask-company"].tap()

        let question = app.textFields["answer-question"]
        XCTAssertTrue(question.waitForExistence(timeout: 2))
        question.tap()
        question.typeText("주요 사업 부문은 무엇인가요")
        app.buttons["answer-submit"].tap()

        XCTAssertTrue(app.descendants(matching: .any)["answer-result"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["반도체와 모바일 사업이 핵심 사업 부문입니다."].exists)
        XCTAssertTrue(app.staticTexts["근거 확인됨"].exists)
    }
    func testAccessibilityOfReaderAndAnswer() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing"]
        app.launch()
        let company = app.buttons["company-005930"].firstMatch
        XCTAssertTrue(company.waitForExistence(timeout: 5))
        try app.performAccessibilityAudit(for: [.contrast, .hitRegion, .sufficientElementDescription])
        company.tap()
        XCTAssertTrue(app.descendants(matching: .any)["digest-screen"].waitForExistence(timeout: 5))
        try app.performAccessibilityAudit(for: [.contrast, .hitRegion, .sufficientElementDescription])
        app.buttons["ask-company"].tap()
        XCTAssertTrue(app.textFields["answer-question"].waitForExistence(timeout: 5))
        try app.performAccessibilityAudit(for: [.contrast, .hitRegion, .sufficientElementDescription])
    }

    func testLargeTextCanReachEnglishReader() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        app.launch()
        let company = app.buttons["company-005930"].firstMatch
        XCTAssertTrue(company.waitForExistence(timeout: 5))
        for _ in 0..<8 where !company.isHittable { app.swipeUp() }
        XCTAssertTrue(company.isHittable)
        company.tap()
        let english = app.buttons["영어"]
        for _ in 0..<8 where !english.isHittable { app.swipeUp() }
        XCTAssertTrue(english.isHittable)
        english.tap()
        let ask = app.buttons["ask-company"]
        for _ in 0..<8 where !ask.isHittable { app.swipeUp() }
        XCTAssertTrue(ask.isHittable)
        ask.tap()
        XCTAssertTrue(app.textFields["answer-question"].waitForExistence(timeout: 5))
    }

    func testDigestPolishStatesInKoreanAndEnglish() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-ui-testing", "-ui-testing-digest-polish"]
        app.launch()

        app.buttons["company-005930"].firstMatch.tap()
        XCTAssertTrue(app.descendants(matching: .any)["digest-screen"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["전년 대비 0%"].exists)
        XCTAssertFalse(app.staticTexts["↑ 전년 대비 0%"].exists)
        XCTAssertTrue(app.staticTexts["+4.1%"].exists)
        XCTAssertTrue(app.staticTexts["-2.4%"].exists)
        XCTAssertTrue(app.staticTexts["전년 비교 자료 없음"].exists)
        attachScreenshot(named: "digest-polish-ko-metrics")

        let summary = app.staticTexts["서술형 요약을 사용할 수 없습니다. 위 재무 수치는 공시 데이터에서 가져온 값으로 그대로 확인할 수 있습니다."]
        for _ in 0..<6 where !summary.isHittable { app.swipeUp() }
        XCTAssertTrue(summary.isHittable)
        XCTAssertTrue(app.staticTexts["근거 공시"].exists)
        XCTAssertTrue(app.staticTexts["· 앱에서 열기"].exists)
        attachScreenshot(named: "digest-polish-ko-summary")

        let english = app.buttons["영어"]
        for _ in 0..<6 where !english.isHittable { app.swipeDown() }
        XCTAssertTrue(english.isHittable)
        english.tap()
        for _ in 0..<6 { app.swipeDown() }
        XCTAssertTrue(app.staticTexts["YoY 0%"].exists)
        XCTAssertFalse(app.staticTexts["↑ YoY 0%"].exists)
        XCTAssertTrue(app.staticTexts["YoY unavailable"].exists)
        attachScreenshot(named: "digest-polish-en-metrics")

        let englishSummary = app.staticTexts["Narrative summary unavailable. The financial figures above remain available from structured filing data."]
        for _ in 0..<6 where !englishSummary.isHittable { app.swipeUp() }
        XCTAssertTrue(englishSummary.isHittable)
        XCTAssertTrue(app.staticTexts["Filing Sources"].exists)
        XCTAssertTrue(app.staticTexts["· Open in app"].exists)
        attachScreenshot(named: "digest-polish-en-summary")
    }

    private func attachScreenshot(named name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
