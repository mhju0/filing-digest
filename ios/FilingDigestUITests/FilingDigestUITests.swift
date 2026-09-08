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
}
