//
//  APIClient.swift
//  Haeksim
//
//  Minimal URLSession-based client for API CONTRACT v0.1.
//  - async/await only, no third-party dependencies.
//  - snake_case JSON handled via key coding strategies, so the Codable models
//    stay camelCase without hand-written CodingKeys.
//  - URLRequest construction is split into internal make*Request functions so
//    unit tests can verify path/query/method/body without hitting the network.
//
//  Note on ATS: the default baseURL is plain HTTP on 127.0.0.1. App Transport
//  Security exempts loopback connections, so no Info.plist exception is
//  required for the local dev backend.
//

import Foundation

// MARK: - Errors

/// Errors surfaced by APIClient. UI code can show `errorDescription` directly.
enum APIError: Error, LocalizedError {
    /// A request URL could not be constructed from baseURL + path + query.
    case invalidURL
    /// The response was not an HTTPURLResponse.
    case invalidResponse
    /// Non-2xx HTTP status.
    case httpStatus(Int)
    /// The body could not be decoded into the expected Codable type.
    case decoding(Error)
    /// URLSession-level failure (connection refused, timeout, ...).
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "요청 URL을 만들 수 없습니다."
        case .invalidResponse:
            return "서버 응답 형식이 올바르지 않습니다."
        case .httpStatus(let code):
            return "서버 오류가 발생했습니다. (HTTP \(code))"
        case .decoding:
            return "서버 응답을 해석할 수 없습니다."
        case .transport:
            return "서버에 연결할 수 없습니다. 백엔드(127.0.0.1:8000)가 실행 중인지 확인하세요."
        }
    }
}

// MARK: - Client

struct APIClient {
    /// Default local dev backend (see repo docker-compose / API contract).
    static let defaultBaseURL = URL(string: "http://127.0.0.1:8000")!

    let baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(baseURL: URL = APIClient.defaultBaseURL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session
        self.decoder = APIClient.makeJSONDecoder()
        self.encoder = APIClient.makeJSONEncoder()
    }

    /// Production decoder configuration, exposed for tests.
    static func makeJSONDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    /// Production encoder configuration, exposed for tests.
    static func makeJSONEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }

    // MARK: Request construction (internal so tests can inspect requests)

    /// Builds a URLRequest against `baseURL`.
    /// Note: `path` replaces any path component of `baseURL`, so baseURL is
    /// expected to be scheme://host:port with no path prefix.
    func makeRequest(
        path: String,
        queryItems: [URLQueryItem] = [],
        method: String = "GET",
        body: Data? = nil
    ) throws -> URLRequest {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw APIError.invalidURL
        }
        components.path = path
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw APIError.invalidURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    /// GET /companies?q=<query>
    func makeCompanySearchRequest(query: String) throws -> URLRequest {
        try makeRequest(
            path: "/companies",
            queryItems: [URLQueryItem(name: "q", value: query)]
        )
    }

    /// GET /companies/{company_id}/digest?lang=ko|en
    func makeDigestRequest(companyID: String, language: Language) throws -> URLRequest {
        try makeRequest(
            path: "/companies/\(companyID)/digest",
            queryItems: [URLQueryItem(name: "lang", value: language.rawValue)]
        )
    }

    /// POST /chat
    func makeChatRequest(_ body: ChatRequest) throws -> URLRequest {
        try makeRequest(path: "/chat", method: "POST", body: encoder.encode(body))
    }

    /// POST /ingest
    func makeIngestRequest(_ body: IngestRequest) throws -> URLRequest {
        try makeRequest(path: "/ingest", method: "POST", body: encoder.encode(body))
    }

    // MARK: Endpoints

    /// GET /companies?q=
    func searchCompanies(query: String) async throws -> CompanySearchResponse {
        try await send(makeCompanySearchRequest(query: query))
    }

    /// GET /companies/{company_id}/digest?lang=
    /// The response always contains both summary_ko and summary_en, so the UI
    /// can toggle languages without refetching.
    func fetchDigest(companyID: String, language: Language = .ko) async throws -> CompanyDigest {
        try await send(makeDigestRequest(companyID: companyID, language: language))
    }

    /// POST /chat
    func sendChat(_ request: ChatRequest) async throws -> ChatResponse {
        try await send(makeChatRequest(request))
    }

    /// POST /ingest (backend answers 202 with a queued job id).
    func startIngest(_ request: IngestRequest) async throws -> IngestResponse {
        try await send(makeIngestRequest(request))
    }

    // MARK: Transport

    /// Executes a request, validates the HTTP status (2xx, which covers both
    /// 200 and the 202 returned by /ingest), and decodes the JSON body.
    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.httpStatus(http.statusCode)
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }
}
