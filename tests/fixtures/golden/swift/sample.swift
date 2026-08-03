import Foundation

protocol Identifiable {
    var id: String { get }
}

enum Status {
    case active
    case inactive
}

class BaseEntity {
    var name: String
    init(name: String) {
        self.name = name
    }
}

class User: BaseEntity, Identifiable {
    var id: String
    var status: Status = .active

    init(id: String, name: String) {
        self.id = id
        super.init(name: name)
    }

    func process() -> String {
        return helperCall(input: name)
    }

    private func helperCall(input: String) -> String {
        return "processed_\(input)"
    }
}
