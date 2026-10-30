import SwiftUI

extension View {
    /// Mount a nonvisual native input adapter with the same lifetime and bounds as its content.
    func nativeInputHost<Input: NSViewRepresentable>(_ host: Input) -> some View {
        background(host)
    }
}

/// Neutral component data; generated from layouts.json. No native view is stored in the design document.
public struct DesignNode: Codable, Sendable {
    public var id: String
    public var kind: String
    public var props: [String: String]
    public var children: [DesignNode]
    public init(id: String, kind: String, props: [String: String] = [:], children: [DesignNode] = []) {
        self.id = id
        self.kind = kind
        self.props = props
        self.children = children
    }
}
public struct DesignContext: Sendable {
    public var values: [String: String]
    public var collections: [String: [DesignContext]]
    public init(_ values: [String: String] = [:], collections: [String: [DesignContext]] = [:]) {
        self.values = values
        self.collections = collections
    }
    public func merging(_ child: Self) -> Self {
        Self(
            values.merging(child.values) { _, v in v },
            collections: collections.merging(child.collections) { _, v in v })
    }
    public func resolving(_ source: String, language: String) -> String {
        if source.hasPrefix("= ") { return expression(String(source.dropFirst(2)), language: language) }
        if source.hasPrefix("$") { return values[String(source.dropFirst())] ?? "" }
        if source.hasPrefix("@") { return SyncWatchLayouts.values[String(source.dropFirst())] ?? "" }
        let composite = NSMutableString(string: source)
        let copyPattern = try? NSRegularExpression(pattern: #"\{~([A-Za-z][A-Za-z0-9]*)\}"#)
        for match in copyPattern?.matches(in: source, range: NSRange(location: 0, length: (source as NSString).length))
            .reversed() ?? []
        {
            let key = (source as NSString).substring(with: match.range(at: 1))
            composite.replaceCharacters(in: match.range, with: SyncWatchCopy.text(key, language: language))
        }
        var output = composite as String
        for (key, value) in values { output = output.replacingOccurrences(of: "{" + key + "}", with: value) }
        return source.hasPrefix("~")
            ? (output.count == 1 ? "" : SyncWatchCopy.text(String(output.dropFirst()), language: language)) : output
    }
    private func expression(_ expression: String, language: String) -> String {
        var stack: [String] = []
        for token in expression.split(separator: " ").map(String.init) {
            if token == "if" {
                guard stack.count >= 3 else { return "" }
                let no = stack.removeLast()
                let yes = stack.removeLast()
                let condition = stack.removeLast()
                stack.append(Self.flag(condition) ? yes : no)
            } else if ["+", "-", "*", "/", "min", "max", "==", ">=", ">", "<"].contains(token) {
                guard stack.count >= 2 else { return "" }
                let right = stack.removeLast()
                let left = stack.removeLast()
                let a = Self.number(left)
                let b = Self.number(right)
                let result: Double
                switch token {
                case "+": result = a + b
                case "-": result = a - b
                case "*": result = a * b
                case "/": result = b == 0 ? 0 : a / b
                case "min": result = min(a, b)
                case "max": result = max(a, b)
                case "==": result = left == right ? 1 : 0
                case ">=": result = a >= b ? 1 : 0
                case ">": result = a > b ? 1 : 0
                default: result = a < b ? 1 : 0
                }
                stack.append(String(result.isFinite ? result : 0))
            } else {
                stack.append(resolving(token, language: language))
            }
        }
        return stack.count == 1 ? stack[0] : ""
    }
    public static func number(_ value: String) -> Double { Double(value).flatMap { $0.isFinite ? $0 : nil } ?? 0 }
    public static func flag(_ value: String) -> Bool { value == "true" || Double(value) == 1 }
}
public struct DesignEvent: Sendable {
    public let action: String
    public let value: String?
    public let payload: String?
    public init(_ action: String, value: String? = nil, payload: String? = nil) {
        self.action = action
        self.value = value
        self.payload = payload
    }
}
public struct DesignElement: Identifiable, Sendable {
    public let id: String
    public let kind: String
    public let props: [String: String]
    public let children: [DesignElement]
    public func string(_ key: String) -> String { props[key] ?? "" }
    public func number(_ key: String) -> Double { DesignContext.number(string(key)) }
    public func flag(_ key: String) -> Bool { DesignContext.flag(string(key)) }
}
public enum DesignTree {
    public static func resolve(_ component: String, context: DesignContext, language: String = "en", viewport: CGSize)
        -> DesignElement?
    {
        guard let source = SyncWatchLayouts.components[component] else { return nil }
        return resolve(source, context: context, language: language, viewport: viewport)
    }
    public static func resolve(_ source: DesignNode, context: DesignContext, language: String = "en", viewport: CGSize)
        -> DesignElement?
    {
        let ctx = context.merging(
            DesignContext([
                "viewport.width": String(Double(viewport.width)), "viewport.height": String(Double(viewport.height)),
            ]))
        return expand(source, ctx, language, source.id, SyncWatchLayouts.defaults).first
    }
    private static func expand(
        _ node: DesignNode, _ ctx: DesignContext, _ language: String, _ path: String, _ inherited: [String: String]
    ) -> [DesignElement] {
        let raw = inherited.merging(node.props) { _, v in v }
        if let condition = raw["when"], !DesignContext.flag(ctx.resolving(condition, language: language)) { return [] }
        var localContext = ctx
        if let height = raw["height"], height != "fill" {
            localContext.values["local.height"] = ctx.resolving(height, language: language)
        }
        let p = raw.mapValues { localContext.resolving($0, language: language) }
        if node.kind == "repeat" {
            return (ctx.collections[p["items"] ?? ""] ?? []).enumerated().flatMap { index, child in
                node.children.flatMap {
                    expand($0, ctx.merging(child), language, path + "." + String(index) + "." + $0.id, inherited)
                }
            }
        }
        if node.kind == "include" {
            guard var included = SyncWatchLayouts.components[p["component"] ?? ""] else { return [] }
            included.props.merge(node.props.filter { $0.key != "component" }) { _, v in v }
            return expand(included, ctx, language, path + "." + included.id, inherited)
        }
        if node.kind == "if" {
            let index = DesignContext.flag(p["condition"] ?? "") ? 0 : 1
            return index < node.children.count
                ? expand(node.children[index], ctx, language, path + "." + node.children[index].id, inherited) : []
        }
        if node.kind == "switch" {
            guard
                let child = node.children.first(where: {
                    DesignContext.flag($0.props["case"] ?? "") && DesignContext.flag(p["value"] ?? "")
                        || $0.props["case"] == p["value"]
                }) ?? node.children.first(where: { $0.props["case"] == "default" })
            else { return [] }
            var selected = child
            selected.props.merge(
                node.props.filter {
                    ["width", "height", "minWidth", "minHeight", "maxWidth", "maxHeight"].contains($0.key)
                }
            ) { _, v in v }
            return expand(selected, ctx, language, path + "." + selected.id, inherited)
        }
        let childStyle = raw.filter { ["fontSize", "fontFamily", "foreground", "weight"].contains($0.key) }
        var children = node.children.flatMap { expand($0, ctx, language, path + "." + $0.id, childStyle) }
        if node.kind == "choice" {
            children = (ctx.collections[p["options"] ?? ""] ?? []).enumerated().map { index, item in
                DesignElement(
                    id: path + ".option" + String(index), kind: "option",
                    props: item.values.merging(["label": ctx.merging(item).resolving("~{label}", language: language)]) {
                        _, v in v
                    }, children: [])
            }
        }
        let kind =
            node.kind == "row" && p["wrapAt"] != nil
                && DesignContext.number(ctx.values["viewport.width"] ?? "") < DesignContext.number(p["wrapAt"] ?? "")
            ? "column" : node.kind
        return [DesignElement(id: path, kind: kind, props: p, children: children)]
    }
}

private struct DesignIcon: Shape {
    var name: String
    func path(in rect: CGRect) -> Path {
        var path = Path()
        for polygon in SyncWatchLayouts.assets[name] ?? [] {
            for i in stride(from: 0, to: polygon.count, by: 2) {
                let point = CGPoint(
                    x: rect.minX + polygon[i] / SyncWatchLayouts.coordinateSize * rect.width,
                    y: rect.minY + polygon[i + 1] / SyncWatchLayouts.coordinateSize * rect.height)
                if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
            }
            path.closeSubpath()
        }
        return path
    }
}
struct DesignPaint {
    var scheme: ColorScheme
    func color(_ value: String) -> Color {
        if value == "transparent" || value.isEmpty { return .clear }
        var hex = value
        if value.hasPrefix("theme.") {
            let key = String(value.dropFirst("theme.".count))
            return SyncWatchLayouts.themeColor(key, theme: .resolve(scheme)) ?? .clear
        }
        if value.hasPrefix("@") { hex = SyncWatchLayouts.values[String(value.dropFirst())] ?? "" }
        guard hex.hasPrefix("#"), hex.count == 9, let rgba = UInt64(hex.dropFirst(), radix: 16) else { return .clear }
        return Color(
            .sRGB, red: Double(rgba >> 24 & 255) / 255, green: Double(rgba >> 16 & 255) / 255,
            blue: Double(rgba >> 8 & 255) / 255, opacity: Double(rgba & 255) / 255)
    }
    func point(_ value: String) -> UnitPoint {
        let values = value.split(separator: ",").map { DesignContext.number(String($0)) }
        return UnitPoint(x: values.first ?? 0, y: values.count > 1 ? values[1] : 0)
    }
    func weight(_ name: String) -> Font.Weight {
        switch name {
        case "heavy": .heavy
        case "bold": .bold
        case "semibold": .semibold
        default: .regular
        }
    }
    func alignment(_ name: String) -> Alignment {
        switch name {
        case "leading": .leading
        case "trailing": .trailing
        case "top": .topLeading
        default: .center
        }
    }
}
private struct DesignContour: Shape {
    var radius: Double
    var circular: Bool
    func path(in rect: CGRect) -> Path {
        circular ? Circle().path(in: rect) : RoundedRectangle(cornerRadius: radius).path(in: rect)
    }
}
private struct DesignDecoration: ViewModifier {
    var node: DesignElement
    var paint: DesignPaint
    private func dimension(_ key: String) -> CGFloat? {
        node.props[key] == nil || node.string(key) == "fill" ? nil : CGFloat(max(0, node.number(key)))
    }
    func body(content: Content) -> some View {
        let radius =
            node.string("radius") == "circle"
            ? max(node.number("width"), node.number("height")) / 2 : node.number("radius")
        content
            .font(.system(size: node.number("fontSize"), weight: paint.weight(node.string("weight"))))
            .foregroundStyle(paint.color(node.string("foreground")))
            .padding(.horizontal, node.number("paddingX") + node.number("padding"))
            .padding(.vertical, node.number("paddingY") + node.number("padding"))
            .frame(
                width: dimension("width"), height: dimension("height"), alignment: paint.alignment(node.string("align"))
            )
            .frame(
                minWidth: dimension("minWidth"),
                maxWidth: node.string("width") == "fill" ? .infinity : dimension("maxWidth"),
                minHeight: dimension("minHeight"),
                maxHeight: node.string("height") == "fill" ? .infinity : dimension("maxHeight"),
                alignment: paint.alignment(node.string("align"))
            )
            .background {
                if !node.string("gradient").isEmpty {
                    LinearGradient(
                        stops: zip(
                            node.string("gradient").split(separator: "|"),
                            node.string("gradientOffsets").split(separator: "|")
                        ).map { .init(color: paint.color(String($0.0)), location: DesignContext.number(String($0.1))) },
                        startPoint: paint.point(node.string("gradientStart")),
                        endPoint: paint.point(node.string("gradientEnd")))
                } else {
                    paint.color(node.string("background")).opacity(
                        node.props["backgroundOpacity"] == nil ? 1 : node.number("backgroundOpacity"))
                }
            }
            .clipShape(DesignContour(radius: radius, circular: node.string("radius") == "circle"))
            .overlay(
                DesignContour(radius: radius, circular: node.string("radius") == "circle").stroke(
                    paint.color(node.string("border")), lineWidth: node.number("borderWidth"))
            )
            .offset(x: node.number("offsetX"), y: node.number("offsetY"))
    }
}
private struct DesignToggleStyle: ToggleStyle {
    var node: DesignElement
    var paint: DesignPaint
    func makeBody(configuration: Configuration) -> some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack {
                configuration.label
                Spacer(minLength: 0)
                RoundedRectangle(cornerRadius: node.number("trackHeight") / 2)
                    .fill(paint.color(node.string(configuration.isOn ? "onFill" : "offFill")))
                    .frame(width: node.number("trackWidth"), height: node.number("trackHeight"))
                    .overlay(alignment: configuration.isOn ? .trailing : .leading) {
                        Circle().fill(paint.color(node.string("thumbFill")))
                            .frame(width: node.number("thumbSize"), height: node.number("thumbSize"))
                            .padding(.horizontal, node.number("thumbInset"))
                    }
            }
        }.buttonStyle(DesignPressStyle(node: node))
    }
}

private struct DesignPressStyle: ButtonStyle {
    var node: DesignElement
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(
            enabled ? configuration.isPressed ? node.number("pressedOpacity") : 1 : node.number("disabledOpacity"))
    }
}

/// Mechanical SwiftUI adapter for the neutral tree. It has no Library/Settings/Player layout branches.
public struct DesignSurface: View {
    @Environment(\.designPresented) private var presented
    public var component: String
    var testingNode: DesignNode?
    public var context: DesignContext
    public var language: String
    public var slots: [String: AnyView]
    public var onEvent: (DesignEvent) -> Void
    @Environment(\.colorScheme) private var scheme
    public init(
        _ component: String, context: DesignContext = DesignContext(), language: String = "en",
        slots: [String: AnyView] = [:], onEvent: @escaping (DesignEvent) -> Void = { _ in }
    ) {
        self.component = component
        self.context = context
        self.language = language
        self.slots = slots
        self.onEvent = onEvent
    }
    init(testingNode: DesignNode, context: DesignContext = DesignContext()) {
        self.component = ""
        self.testingNode = testingNode
        self.context = context
        self.language = "en"
        self.slots = [:]
        self.onEvent = { _ in }
    }
    public var body: some View {
        GeometryReader { geometry in
            if let tree = testingNode.flatMap({
                DesignTree.resolve($0, context: context, language: language, viewport: geometry.size)
            }) ?? DesignTree.resolve(component, context: context, language: language, viewport: geometry.size) {
                render(tree)
            }
        }
        .environment(\.locale, Locale(identifier: language))
        .frame(width: rootDimension("width"), height: rootDimension("height"))
        .frame(minWidth: rootDimension("minWidth"), minHeight: rootDimension("minHeight"))
    }
    private func rootDimension(_ key: String) -> CGFloat? {
        guard let source = (testingNode ?? SyncWatchLayouts.components[component])?.props[key], source != "fill" else {
            return nil
        }
        return CGFloat(DesignContext.number(context.resolving(source, language: language)))
    }
    private var paint: DesignPaint { DesignPaint(scheme: scheme) }
    private func emit(_ node: DesignElement, value: String? = nil) {
        guard node.props["enabled"] == nil || node.flag("enabled") else { return }
        onEvent(DesignEvent(node.string("action"), value: value ?? node.props["value"], payload: node.props["payload"]))
    }
    private func binding(_ node: DesignElement) -> Binding<String> {
        Binding(get: { node.string("value") }, set: { emit(node, value: $0) })
    }
    private func decorated<V: View>(_ content: V, _ node: DesignElement) -> AnyView {
        AnyView(content.modifier(DesignDecoration(node: node, paint: paint)))
    }
    private func render(_ node: DesignElement) -> AnyView {
        let enabled = node.props["enabled"] == nil || node.flag("enabled")
        let result: AnyView
        switch node.kind {
        case "row":
            result = decorated(
                HStack(alignment: node.string("align") == "top" ? .top : .center, spacing: node.number("gap")) {
                    ForEach(node.children) { render($0) }
                }, node)
        case "column":
            result = decorated(
                VStack(alignment: node.string("align") == "center" ? .center : .leading, spacing: node.number("gap")) {
                    ForEach(node.children) { render($0) }
                }, node)
        case "overlay": result = decorated(ZStack { ForEach(node.children) { render($0) } }, node)
        case "scroll": result = decorated(ScrollView { ForEach(node.children) { render($0) } }, node)
        case "spacer": result = AnyView(Spacer(minLength: 0))
        case "line":
            let thickness = CGFloat(node.number("thickness"))
            let length: CGFloat? = node.props["length"] == nil ? nil : CGFloat(node.number("length"))
            let vertical = node.string("axis") == "vertical"
            result = AnyView(
                paint.color(node.string("background")).frame(
                    width: vertical ? thickness : length, height: vertical ? length : thickness))
        case "text":
            var text = AnyView(
                Text(node.string("text")).lineLimit(node.props["lines"].flatMap(Int.init)).truncationMode(
                    node.string("truncate") == "middle" ? .middle : .tail
                ).multilineTextAlignment(node.string("align") == "center" ? .center : .leading).minimumScaleFactor(
                    node.props["minScale"] == nil ? 1 : node.number("minScale")))
            if !node.string("moveAction").isEmpty {
                text = AnyView(
                    text.contentShape(Rectangle()).gesture(
                        DragGesture().onChanged {
                            onEvent(
                                DesignEvent(
                                    node.string("moveAction"), value: "\($0.translation.width),\($0.translation.height)"
                                ))
                        }.onEnded { _ in onEvent(DesignEvent(node.string("moveAction"))) }))
            }
            result = decorated(text, node)
        case "icon": result = decorated(DesignIcon(name: node.string("name")), node)
        case "button":
            var button = AnyView(
                Button {
                    emit(node)
                } label: {
                    if node.children.isEmpty {
                        decorated(Text(node.props["text"] ?? node.string("label")), node)
                    } else {
                        decorated(ForEach(node.children) { render($0) }, node)
                    }
                }.buttonStyle(DesignPressStyle(node: node)).disabled(!enabled))
            if node.string("shortcut") == "default" { button = AnyView(button.keyboardShortcut(.defaultAction)) }
            if node.string("shortcut") == "cancel" { button = AnyView(button.keyboardShortcut(.cancelAction)) }
            if !node.string("popoverSlot").isEmpty {
                button = AnyView(
                    button.popover(
                        isPresented: Binding(
                            get: { presented && slots[node.string("popoverSlot")] != nil },
                            set: { if !$0 { onEvent(DesignEvent(node.string("popoverDismiss"))) } }
                        ), arrowEdge: node.string("popoverEdge") == "top" ? .bottom : .top
                    ) { slots[node.string("popoverSlot")] }.onChange(of: presented) { active in
                        if !active, slots[node.string("popoverSlot")] != nil {
                            onEvent(DesignEvent(node.string("popoverDismiss")))
                        }
                    })
            }
            result = button
        case "input":
            result = decorated(
                Group {
                    if node.flag("secret") {
                        SecureField(node.string("label"), text: binding(node))
                    } else {
                        TextField(node.string("label"), text: binding(node))
                    }
                }.textFieldStyle(.plain).disabled(!enabled), node)
        case "choice":
            let options = contextForOptions(node)
            let selected =
                options.first { $0.values["id"] == node.string("value") }?.values["label"] ?? node.string("value")
            result = decorated(
                Menu {
                    ForEach(options.indices, id: \.self) { i in
                        Button(options[i].values["label"] ?? "") { emit(node, value: options[i].values["id"]) }
                    }
                } label: {
                    Text(selected).frame(maxWidth: .infinity, alignment: .leading)
                        .overlay(alignment: .trailing) {
                            DesignIcon(name: node.string("markName")).fill(paint.color(node.string("markFill"))).frame(
                                width: node.number("markWidth"), height: node.number("markHeight"))
                        }
                }.menuStyle(.borderlessButton).menuIndicator(.hidden).disabled(!enabled), node)
        case "toggle":
            result = decorated(
                Toggle(
                    node.string("text"),
                    isOn: Binding(get: { node.flag("value") }, set: { emit(node, value: String($0)) })
                ).toggleStyle(DesignToggleStyle(node: node, paint: paint)).disabled(!enabled), node)
        case "gesture":
            result = decorated(
                DesignGesture(node: node, content: AnyView(ForEach(node.children) { render($0) })) { value in
                    emit(node, value: value)
                }, node)
        case "range": result = AnyView(DesignRange(node: node, paint: paint) { emit(node, value: String($0)) })
        case "slot": result = decorated(slots[node.string("name")] ?? AnyView(Color.clear), node)
        case "retained":
            result = decorated(
                RetainedContent(
                    content: AnyView(ForEach(node.children) { render($0) }), presented: node.flag("presented")),
                node)
        case "subtitle":
            result = AnyView(DesignSubtitle(node: node, paint: paint, context: context, language: language))
        default: result = AnyView(EmptyView())
        }
        var accessible = AnyView(
            result.accessibilityIdentifier(node.string("automation"))
                .accessibilityAddTraits(node.flag("selected") ? .isSelected : []))
        // An absent override must preserve the native label of text and button content.
        if !node.string("label").isEmpty {
            accessible = AnyView(accessible.accessibilityLabel(node.string("label")))
        }
        return accessible
    }
    private func contextForOptions(_ node: DesignElement) -> [DesignContext] {
        // Expanded option data is attached to each primitive, keeping field binding separate from view construction.
        node.children.map { DesignContext($0.props) }
    }
}

private struct DesignRange: View {
    var node: DesignElement
    var paint: DesignPaint
    var onChange: (Double) -> Void
    @FocusState private var focused: Bool
    private var enabled: Bool { node.props["enabled"] == nil || node.flag("enabled") }
    private var minimum: Double { node.number("minimum") }
    private var maximum: Double { max(minimum, node.number("maximum")) }
    private var value: Double { min(maximum, max(minimum, node.number("value"))) }
    private func update(_ value: Double) { if enabled { onChange(node.flag("round") ? value.rounded() : value) } }
    var body: some View {
        GeometryReader { geometry in
            let radius = node.number("thumbSize") / 2
            let span = max(1, geometry.size.width - radius * 2)
            let fraction = (value - minimum) / max(1, maximum - minimum)
            ZStack(alignment: .leading) {
                Capsule().fill(paint.color(node.string("trackFill"))).frame(height: node.number("trackHeight")).padding(
                    .horizontal, radius)
                Capsule().fill(paint.color(node.string("progressFill"))).frame(
                    width: span * fraction, height: node.number("trackHeight")
                ).offset(x: radius)
                Circle().fill(paint.color(node.string("thumbFill"))).frame(
                    width: node.number("thumbSize"), height: node.number("thumbSize")
                ).offset(x: span * fraction)
            }.frame(maxWidth: .infinity, maxHeight: .infinity).contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0).onChanged { drag in
                        guard enabled else { return }
                        focused = true
                        update(
                            min(
                                maximum,
                                max(minimum, minimum + Double((drag.location.x - radius) / span) * (maximum - minimum)))
                        )
                    })
        }.frame(
            width: node.string("width") == "fill" || node.props["width"] == nil ? nil : node.number("width"),
            height: node.number("height")
        )
        .opacity(enabled ? 1 : node.number("disabledOpacity")).focusable(enabled).focused($focused)
        .overlay(
            RoundedRectangle(cornerRadius: node.number("focusRadius")).stroke(
                focused ? paint.color(node.string("focusBorder")) : .clear, lineWidth: node.number("borderWidth"))
        )
        .onMoveCommand { direction in
            if direction == .left || direction == .down { update(max(minimum, value - node.number("step"))) }
            if direction == .right || direction == .up { update(min(maximum, value + node.number("step"))) }
        }
        .accessibilityElement(children: .ignore).accessibilityLabel(node.string("label")).accessibilityValue(
            String(value)
        )
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: update(min(maximum, value + node.number("step")))
            case .decrement: update(max(minimum, value - node.number("step")))
            @unknown default: break
            }
        }.disabled(!enabled)
    }
}

struct DesignSubtitle: View {
    var node: DesignElement
    var paint: DesignPaint
    var context: DesignContext
    var language: String
    var body: some View {
        GeometryReader { geometry in
            let y = node.number("centerY")
            let count = max(1, Int(node.number("outlineSamples")))
            let fontDesign: Font.Design =
                node.string("fontFamily") == "serif"
                ? .serif : node.string("fontFamily") == "monospace" ? .monospaced : .default
            let label = Text(node.string("text"))
            ZStack {
                ForEach(0..<count, id: \.self) { index in
                    label.foregroundStyle(paint.color(node.string("outline"))).offset(
                        x: cos(Double(index) * .pi * 2 / Double(count)) * node.number("outlineWidth"),
                        y: sin(Double(index) * .pi * 2 / Double(count)) * node.number("outlineWidth"))
                }
                label.foregroundStyle(paint.color(node.string("foreground")))
            }.font(
                .system(size: node.number("fontSize"), weight: paint.weight(node.string("weight")), design: fontDesign)
            )
            .multilineTextAlignment(.center).lineLimit(Int(node.number("lines"))).frame(
                width: max(1, geometry.size.width - node.number("textPaddingX") * 2)
            )
            .position(x: geometry.size.width / 2, y: y)
        }.frame(height: node.number("height")).background(paint.color(node.string("background"))).clipped()
            .accessibilityLabel(node.string("text"))
    }
}

/// Mechanical two-axis pointer/keyboard primitive. Its mode, bounds and step belong to the document.
private struct DesignGesture: View {
    var node: DesignElement
    var content: AnyView
    var onChange: (String) -> Void
    @State private var previous = CGSize.zero
    @State private var origin: CGSize?
    private var enabled: Bool { node.props["enabled"] == nil || node.flag("enabled") }
    private func publish(_ x: Double, _ y: Double, origin: CGSize) {
        guard enabled else { return }
        let extent = node.string("gestureMode") == "extent"
        let a = extent ? min(node.number("maximumX"), max(node.number("minimumX"), origin.width + x)) : x
        let b = extent ? min(node.number("maximumY"), max(node.number("minimumY"), origin.height + y)) : y
        onChange("\(a),\(b)")
    }
    private var currentOrigin: CGSize { CGSize(width: node.number("originWidth"), height: node.number("originHeight")) }
    var body: some View {
        content.contentShape(Rectangle()).gesture(
            DragGesture().onChanged { value in
                guard enabled else { return }
                if origin == nil { origin = currentOrigin }
                let delta =
                    node.string("gestureMode") == "extent"
                    ? value.translation
                    : CGSize(
                        width: value.translation.width - previous.width,
                        height: value.translation.height - previous.height)
                previous = value.translation
                publish(delta.width, delta.height, origin: origin ?? currentOrigin)
            }.onEnded { _ in
                previous = .zero
                origin = nil
            }
        )
        .focusable(enabled).onMoveCommand { direction in
            let step = node.number("gestureStep")
            publish(
                direction == .left ? -step : direction == .right ? step : 0,
                direction == .up ? -step : direction == .down ? step : 0, origin: currentOrigin)
        }
        .accessibilityLabel(node.string("label")).accessibilityAction(named: Text(node.string("label"))) {
            let step = node.number("gestureStep")
            publish(step, node.string("gestureMode") == "extent" ? step : 0, origin: currentOrigin)
        }
    }
}
