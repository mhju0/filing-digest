# Digest final-polish verification

These screenshots were captured on 2026-09-11 from the native SwiftUI app on
the iPhone 17 Pro simulator. They use the deterministic
`-ui-testing-digest-polish` fixture; they are verification evidence, not live
corpus output.

- `digest-polish-ko.png` shows Korean section, action, source, and YoY copy.
- `digest-polish-en.png` shows the same digest after its local language switch.

Both captures include positive, negative, zero, and missing YoY states. Zero has
no upward arrow, and the unavailable narrative explains that structured figures
remain visible.

The source `.xcresult` was produced by:

```sh
xcodebuild test -project ios/FilingDigest.xcodeproj -scheme FilingDigest \
  -destination 'platform=iOS Simulator,id=<available-UDID>' \
  CODE_SIGNING_ALLOWED=NO \
  -only-testing:FilingDigestUITests/FilingDigestUITests/testDigestPolishStatesInKoreanAndEnglish
```
