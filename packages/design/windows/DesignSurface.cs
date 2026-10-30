using System.Globalization;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;

namespace SyncWatch.Design;

// Mechanical WPF adapter. Node kinds are framework primitives; there are no per-screen layout branches.
public class DesignSurface : UserControl
{
    public string Component { get; }
    private readonly DesignNode? testingNode;
    private DesignNode? Source => testingNode ?? SyncWatchLayouts.Components.GetValueOrDefault(Component);
    public DesignContext Context { get; private set; } = new();
    public string UiLanguage { get; private set; } = "en";
    public Dictionary<string, UIElement> Slots { get; } = new();
    public event Action<DesignEvent>? EventRequested;
    private readonly List<Action> bindings = [];
    private Dictionary<string, DesignElement> elements = new();
    private sealed record CachedView(string Kind, FrameworkElement Core, FrameworkElement Frame);
    private Dictionary<string, CachedView> views = new();
    private Dictionary<string, CachedView> buildingViews = new();
    private string shape = "";
    private bool updating;
    private double viewportWidth, viewportHeight;
    public DesignSurface(string component)
    {
        Component = component; Focusable = false;
        if (SyncWatchLayouts.Defaults.GetValueOrDefault("foreground", "").StartsWith("theme.", StringComparison.Ordinal)) SetResourceReference(ForegroundProperty, SyncWatchLayouts.Defaults["foreground"] + ".brush");
        Render(new());
    }
    internal DesignSurface(DesignNode testingNode) : this("") { this.testingNode = testingNode; Render(new()); }
    public void ConfigureHost(FrameworkElement host)
    {
        host.Width = Width; host.Height = Height; host.MinWidth = MinWidth; host.MinHeight = MinHeight;
        if (Source?.Props.GetValueOrDefault("cycleFocus") == "true") KeyboardNavigation.SetTabNavigation(host, KeyboardNavigationMode.Cycle);
    }
    protected override void OnPropertyChanged(DependencyPropertyChangedEventArgs e)
    {
        base.OnPropertyChanged(e);
        if (e.Property == ForegroundProperty && Component is not null && elements is not null) UpdateTree();
    }
    public void Render(DesignContext context, string language = "en")
    {
        VerifyAccess();
        Context = context; UiLanguage = language; Language = System.Windows.Markup.XmlLanguage.GetLanguage(language);
        var source = Source; if (source is null) return;
        double? Dimension(string key) => source.Props.TryGetValue(key, out var value) && value != "fill" ? Math.Max(0, DesignContext.Number(context.Resolve(value, language))) : null;
        Width = Dimension("width") ?? double.NaN; Height = Dimension("height") ?? double.NaN; MinWidth = Dimension("minWidth") ?? 0; MinHeight = Dimension("minHeight") ?? 0;
        if (source.Props.GetValueOrDefault("cycleFocus") == "true") KeyboardNavigation.SetTabNavigation(this, KeyboardNavigationMode.Cycle);
        UpdateTree();
    }
    protected override Size MeasureOverride(Size constraint)
    {
        double width = double.IsFinite(constraint.Width) ? constraint.Width : double.IsFinite(Width) ? Width : MinWidth;
        double height = double.IsFinite(constraint.Height) ? constraint.Height : double.IsFinite(Height) ? Height : MinHeight;
        if (width != viewportWidth || height != viewportHeight) { viewportWidth = width; viewportHeight = height; UpdateTree(); }
        return base.MeasureOverride(constraint);
    }
    private void UpdateTree()
    {
        if (updating) return;
        updating = true;
        try
        {
            var tree = Source is DesignNode source ? DesignTree.Resolve(source, Context, UiLanguage, viewportWidth, viewportHeight) : null; if (tree is null) return;
            static IEnumerable<DesignElement> Walk(DesignElement n) { yield return n; foreach (var child in n.Children) foreach (var next in Walk(child)) yield return next; }
            var nodes = Walk(tree).ToArray();
            elements = nodes.ToDictionary(n => n.Id);
            string nextShape = string.Join('|', nodes.Select(n => n.Id + ":" + n.Kind + ":" + n.Children.Length));
            // Value-only observations update existing controls, preserving focus/caret and suppressing input echoes.
            if (nextShape != shape)
            {
                bindings.Clear(); shape = nextShape; buildingViews = new();
                var content = Build(tree.Id); if (!ReferenceEquals(Content, content)) Content = content;
                foreach (var pair in views)
                    if (!buildingViews.ContainsKey(pair.Key) && pair.Value.Kind == "slot" && pair.Value.Core is Grid removed) removed.Children.Clear();
                views = buildingViews;
            }
            foreach (var refresh in bindings) refresh();
        }
        finally { updating = false; }
    }
    private void Bind(string id, Action<DesignElement> apply) { bindings.Add(() => apply(elements[id])); apply(elements[id]); }
    private void Emit(string id, string? value = null)
    {
        if (updating || !elements.TryGetValue(id, out var node)) return;
        if (node.Props.ContainsKey("enabled") && !node.Flag("enabled")) return;
        EventRequested?.Invoke(new(node.Text("action"), value ?? node.Props.GetValueOrDefault("value"), node.Props.GetValueOrDefault("payload")));
    }
    private FrameworkElement Build(string id)
    {
        var node = elements[id]; FrameworkElement core;
        var previous = views.GetValueOrDefault(id);
        if (previous?.Kind != node.Kind) previous = null;
        bool fresh = previous is null;
        T Existing<T>(Func<T> factory) where T : FrameworkElement => previous?.Core as T ?? factory();
        switch (node.Kind)
        {
            case "row":
            case "column":
                var grid = Existing(() => new Grid()); bool row = node.Kind == "row";
                grid.ColumnDefinitions.Clear(); grid.RowDefinitions.Clear(); var desired = new List<UIElement>();
                for (int index = 0; index < node.Children.Length; index++)
                {
                    if (index > 0) { if (row) grid.ColumnDefinitions.Add(new()); else grid.RowDefinitions.Add(new()); }
                    if (row) grid.ColumnDefinitions.Add(new()); else grid.RowDefinitions.Add(new());
                    var child = Build(node.Children[index].Id); if (row) Grid.SetColumn(child, index * 2); else Grid.SetRow(child, index * 2); desired.Add(child);
                }
                Reconcile(grid, desired);
                Bind(id, n =>
                {
                    for (int i = 0; i < n.Children.Length; i++)
                    {
                        var view = (FrameworkElement)grid.Children[i];
                        var child = n.Children[i];
                        if (row && child.Text("height") != "fill") view.VerticalAlignment = n.Text("align") == "top" ? VerticalAlignment.Top : VerticalAlignment.Center;
                        if (!row && child.Props.ContainsKey("width") && child.Text("width") != "fill") view.HorizontalAlignment = n.Text("align") == "center" ? HorizontalAlignment.Center : HorizontalAlignment.Left;
                    }
                });
                Bind(id, n => { grid.HorizontalAlignment = row && n.Text("align") == "trailing" ? HorizontalAlignment.Right : HorizontalAlignment.Stretch; for (int i = 0; i < n.Children.Length; i++) { var child = n.Children[i]; var size = child.Text(row ? "width" : "height") == "fill" || child.Kind == "spacer" ? new GridLength(1, GridUnitType.Star) : GridLength.Auto; if (row) { grid.ColumnDefinitions[i * 2].Width = size; if (i > 0) grid.ColumnDefinitions[i * 2 - 1].Width = new GridLength(n.Number("gap")); } else { grid.RowDefinitions[i * 2].Height = size; if (i > 0) grid.RowDefinitions[i * 2 - 1].Height = new GridLength(n.Number("gap")); } } }); core = grid; break;
            case "overlay": var overlay = Existing(() => new Grid()); Reconcile(overlay, node.Children.Select(child => (UIElement)Build(child.Id)).ToArray()); core = overlay; break;
            case "retained":
                var retained = Existing(() => new Grid()); Reconcile(retained, node.Children.Select(child => (UIElement)Build(child.Id)).ToArray());
                Bind(id, n =>
                {
                    bool presented = n.Flag("presented");
                    if (!presented && retained.IsKeyboardFocusWithin) Keyboard.ClearFocus();
                    retained.Visibility = presented ? Visibility.Visible : Visibility.Hidden;
                    retained.IsEnabled = presented;
                    retained.IsHitTestVisible = presented;
                });
                core = retained; break;
            case "scroll":
                var scroll = Existing(() => new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
                var scrollChild = node.Children.Length == 0 ? null : Build(node.Children[0].Id);
                if (!ReferenceEquals(scroll.Content, scrollChild)) { Detach(scrollChild); scroll.Content = scrollChild; }
                core = scroll; break;
            case "spacer": core = Existing(() => new Border()); break;
            case "line":
                var line = Existing(() => new Border()); Bind(id, n => { line.Width = n.Text("axis") == "vertical" ? n.Number("thickness") : n.Props.ContainsKey("length") ? n.Number("length") : double.NaN; line.Height = n.Text("axis") == "vertical" ? n.Props.ContainsKey("length") ? n.Number("length") : double.NaN : n.Number("thickness"); Paint(line, Border.BackgroundProperty, n.Text("background")); }); buildingViews[id] = new(node.Kind, line, line); return line;
            case "text":
                var text = Existing(() => new TextBlock { TextWrapping = TextWrapping.Wrap }); Bind(id, n => { text.Text = n.Text("text"); text.TextTrimming = n.Props.ContainsKey("lines") ? TextTrimming.CharacterEllipsis : TextTrimming.None; text.TextWrapping = n.Number("lines") == 1 ? TextWrapping.NoWrap : TextWrapping.Wrap; text.TextAlignment = n.Text("align") == "center" ? TextAlignment.Center : TextAlignment.Left; });
                core = text; break;
            case "icon":
                var viewbox = Existing(() => new Viewbox());
                var canvas = viewbox.Child as Canvas ?? new Canvas { Width = SyncWatchLayouts.CoordinateSize, Height = SyncWatchLayouts.CoordinateSize };
                var path = canvas.Children.OfType<System.Windows.Shapes.Path>().FirstOrDefault() ?? new System.Windows.Shapes.Path { Stretch = Stretch.None };
                if (canvas.Children.Count == 0) canvas.Children.Add(path); viewbox.Child = canvas;
                Bind(id, n => { path.Data = Geometry(n.Text("name")); Paint(path, System.Windows.Shapes.Shape.FillProperty, n.Text("foreground")); }); core = viewbox; break;
            case "button":
                var button = Existing(() => new Button()); button.SetResourceReference(StyleProperty, "Design.Button");
                if (!string.IsNullOrEmpty(node.Text("popoverSlot")))
                {
                    var popup = button.Tag as Popup ?? new Popup { PlacementTarget = button, StaysOpen = false, AllowsTransparency = true };
                    if (button.Tag is not Popup)
                    {
                        button.Tag = popup;
                        IInputElement? returnFocus = null;
                        popup.Opened += (_, _) =>
                        {
                            returnFocus = Keyboard.FocusedElement;
                            if (popup.Child is UIElement child)
                                child.MoveFocus(new TraversalRequest(FocusNavigationDirection.First));
                        };
                        popup.Closed += (_, _) =>
                        {
                            if (returnFocus is UIElement { IsVisible: true } target) target.Focus();
                            returnFocus = null;
                            if (!updating && elements.TryGetValue(id, out var current) && Slots.ContainsKey(current.Text("popoverSlot")))
                                EventRequested?.Invoke(new(current.Text("popoverDismiss")));
                        };
                        popup.PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) { popup.IsOpen = false; e.Handled = true; } };
                        button.Unloaded += (_, _) => popup.IsOpen = false;
                        button.IsVisibleChanged += (_, _) =>
                        {
                            if (button.IsVisible) return;
                            popup.IsOpen = false;
                            Dispatcher.BeginInvoke(new Action(() =>
                            {
                                if (!button.IsVisible && elements.TryGetValue(id, out var current) && Slots.ContainsKey(current.Text("popoverSlot")))
                                    EventRequested?.Invoke(new(current.Text("popoverDismiss")));
                            }));
                        };
                    }
                    Bind(id, n =>
                    {
                        popup.Placement = n.Text("popoverEdge") == "top" ? PlacementMode.Top : PlacementMode.Bottom;
                        var content = Slots.GetValueOrDefault(n.Text("popoverSlot"));
                        if (!ReferenceEquals(popup.Child, content)) { if (content is FrameworkElement element) Detach(element); popup.Child = content; }
                        popup.IsOpen = content is not null && button.IsVisible;
                    });
                    if (fresh) button.Loaded += (_, _) => UpdateTree();
                }
                var buttonChild = node.Children.Length == 0 ? button.Content as FrameworkElement ?? new TextBlock() : node.Children.Length == 1 ? Build(node.Children[0].Id) : new Grid();
                if (!ReferenceEquals(button.Content, buttonChild)) { Detach(buttonChild); button.Content = buttonChild; }
                Bind(id, n => { if (n.Children.Length == 0 && button.Content is TextBlock caption) caption.Text = n.Props.GetValueOrDefault("text", n.Text("label")); button.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); button.IsDefault = n.Text("shortcut") == "default"; button.IsCancel = n.Text("shortcut") == "cancel"; button.TabIndex = n.Props.ContainsKey("tabIndex") ? (int)n.Number("tabIndex") : 0; button.HorizontalContentAlignment = n.Text("contentAlign") == "leading" ? HorizontalAlignment.Stretch : HorizontalAlignment.Center; }); if (fresh) button.Click += (_, _) => Emit(id); core = button; break;
            case "input":
                if (node.Flag("secret"))
                {
                    var secret = Existing(() => new PasswordBox()); secret.SetResourceReference(StyleProperty, "Design.SecretInput");
                    Bind(id, n => { if (secret.Password != n.Text("value")) secret.Password = n.Text("value"); secret.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); });
                    if (previous?.Core is not PasswordBox) secret.PasswordChanged += (_, _) => Emit(id, secret.Password);
                    core = secret;
                }
                else
                {
                    var input = Existing(() => new TextBox()); input.SetResourceReference(StyleProperty, "Design.Input");
                    Bind(id, n => { if (input.Text != n.Text("value")) input.Text = n.Text("value"); input.Tag = n.Text("label"); input.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); });
                    if (previous?.Core is not TextBox) input.TextChanged += (_, _) => Emit(id, input.Text); core = input;
                }
                break;
            case "choice":
                var choice = Existing(() => new ComboBox { DisplayMemberPath = "Value", SelectedValuePath = "Key" }); choice.SetResourceReference(StyleProperty, "Design.Choice");
                Bind(id, n => { var options = n.Children.Select(o => new KeyValuePair<string, string>(o.Text("id"), o.Text("label"))).ToArray(); if (choice.ItemsSource is not KeyValuePair<string, string>[] current || !current.SequenceEqual(options)) choice.ItemsSource = options; if (!Equals(choice.SelectedValue, n.Text("value"))) choice.SelectedValue = n.Text("value"); choice.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); }); if (fresh) choice.SelectionChanged += (_, _) => { if (choice.SelectedValue is string value) Emit(id, value); }; core = choice; break;
            case "toggle":
                var toggle = Existing(() => new CheckBox()); toggle.SetResourceReference(StyleProperty, "Design.Toggle"); Bind(id, n => { toggle.Content = n.Text("text"); toggle.IsChecked = n.Flag("value"); toggle.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); }); if (fresh) { toggle.Checked += (_, _) => Emit(id, "true"); toggle.Unchecked += (_, _) => Emit(id, "false"); }
                core = toggle; break;
            case "gesture":
                var gesture = Existing(() => new Thumb { Focusable = true });
                var gestureChild = node.Children.Length == 1 ? Build(node.Children[0].Id) : null;
                if (!ReferenceEquals(gesture.Tag, gestureChild))
                {
                    gesture.Template = null; gesture.ApplyTemplate(); Detach(gestureChild);
                    var presenter = new FrameworkElementFactory(typeof(ContentPresenter)); presenter.SetBinding(ContentPresenter.ContentProperty, new System.Windows.Data.Binding("Tag") { RelativeSource = new System.Windows.Data.RelativeSource(System.Windows.Data.RelativeSourceMode.TemplatedParent) });
                    gesture.Template = new ControlTemplate(typeof(Thumb)) { VisualTree = presenter }; gesture.Tag = gestureChild;
                }
                Bind(id, n => gesture.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"));
                if (fresh)
                {
                    Point? origin = null; Vector total = new();
                    Point CurrentOrigin() => new(elements[id].Number("originWidth"), elements[id].Number("originHeight"));
                    void Publish(double dx, double dy, Point basis)
                    {
                        var n = elements[id]; bool extent = n.Text("gestureMode") == "extent";
                        double x = extent ? Math.Clamp(basis.X + dx, n.Number("minimumX"), n.Number("maximumX")) : dx;
                        double y = extent ? Math.Clamp(basis.Y + dy, n.Number("minimumY"), n.Number("maximumY")) : dy;
                        Emit(id, x.ToString(CultureInfo.InvariantCulture) + "," + y.ToString(CultureInfo.InvariantCulture));
                    }
                    gesture.DragStarted += (_, _) => { origin = CurrentOrigin(); total = new(); };
                    gesture.DragCompleted += (_, _) => { origin = null; total = new(); };
                    gesture.DragDelta += (_, e) => { origin ??= CurrentOrigin(); total += new Vector(e.HorizontalChange, e.VerticalChange); var extent = elements[id].Text("gestureMode") == "extent"; Publish(extent ? total.X : e.HorizontalChange, extent ? total.Y : e.VerticalChange, origin.Value); };
                    gesture.KeyDown += (_, e) =>
                    {
                        double step = elements[id].Number("gestureStep");
                        if (e.Key is Key.Left or Key.Right or Key.Up or Key.Down) { Publish(e.Key == Key.Left ? -step : e.Key == Key.Right ? step : 0, e.Key == Key.Up ? -step : e.Key == Key.Down ? step : 0, CurrentOrigin()); e.Handled = true; }
                    };
                }
                core = gesture; break;
            case "range":
                var range = Existing(() => new Slider()); range.SetResourceReference(StyleProperty, "Design.Range"); Bind(id, n => { range.Minimum = n.Number("minimum"); range.Maximum = Math.Max(range.Minimum, n.Number("maximum")); range.Value = Math.Clamp(n.Number("value"), range.Minimum, range.Maximum); range.SmallChange = n.Number("step"); range.LargeChange = n.Number("step"); range.IsEnabled = !n.Props.ContainsKey("enabled") || n.Flag("enabled"); }); if (fresh) range.ValueChanged += (_, e) => Emit(id, (elements[id].Flag("round") ? Math.Round(e.NewValue) : e.NewValue).ToString(CultureInfo.InvariantCulture)); core = range; break;
            case "slot":
                var slot = Existing(() => new Grid { ClipToBounds = true }); Bind(id, n => { Slots.TryGetValue(n.Text("name"), out var content); if (slot.Children.Count == 1 && ReferenceEquals(slot.Children[0], content)) return; slot.Children.Clear(); if (content is not null) { if (VisualTreeHelper.GetParent(content) is Panel parent) parent.Children.Remove(content); slot.Children.Add(content); } }); core = slot; break;
            case "subtitle":
                var subtitle = Existing(() => new DesignSubtitle()); Bind(id, n => subtitle.Update(n, UiLanguage)); core = subtitle; break;
            default: core = Existing(() => new Border()); break;
        }
        if (core is Control control)
        {
            Bind(id, n => { Frame(control, n); control.RenderTransform = new TranslateTransform(n.Number("offsetX"), n.Number("offsetY")); Visual(control, n); ResourceValues(control, n); AutomationProperties.SetAutomationId(control, n.Text("automation")); AutomationProperties.SetName(control, n.Text("label")); });
            buildingViews[id] = new(node.Kind, core, control);
            return control;
        }
        var border = previous?.Frame as Border ?? new Border { Child = core };
        if (fresh)
        {
            border.SizeChanged += (_, _) => { if (elements.TryGetValue(id, out var n)) ClipFrame(border, n); };
            Point? origin = null;
            IInputElement? coordinateSpace = null;
            border.MouseLeftButtonDown += (_, e) =>
            {
                if (!elements.TryGetValue(id, out var current) || current.Text("moveAction").Length == 0 || Mouse.LeftButton != MouseButtonState.Pressed) return;
                EventRequested?.Invoke(new(current.Text("moveAction"), Payload: "begin"));
                if (Mouse.LeftButton != MouseButtonState.Pressed) return;
                coordinateSpace = Window.GetWindow(this) ?? (IInputElement)this;
                origin = Mouse.GetPosition(coordinateSpace); border.CaptureMouse(); e.Handled = true;
            };
            border.MouseMove += (_, _) =>
            {
                if (origin is not Point start || coordinateSpace is null || !border.IsMouseCaptured || !elements.TryGetValue(id, out var current)) return;
                var delta = Mouse.GetPosition(coordinateSpace) - start;
                EventRequested?.Invoke(new(current.Text("moveAction"), delta.X.ToString(CultureInfo.InvariantCulture) + "," + delta.Y.ToString(CultureInfo.InvariantCulture), "drag"));
            };
            border.MouseLeftButtonUp += (_, _) =>
            {
                if (origin is null) return; origin = null; border.ReleaseMouseCapture();
                if (elements.TryGetValue(id, out var current)) EventRequested?.Invoke(new(current.Text("moveAction"), Payload: "end"));
            };
        }

        Bind(id, n =>
        {
            // The retained frame itself participates in WPF hit testing even with a transparent brush.
            if (n.Kind == "retained")
            {
                bool presented = n.Flag("presented");
                border.Visibility = presented ? Visibility.Visible : Visibility.Hidden;
                border.IsEnabled = presented; border.IsHitTestVisible = presented;
            }
            Frame(border, n); border.RenderTransform = new TranslateTransform(n.Number("offsetX"), n.Number("offsetY")); border.Padding = Insets(n); border.CornerRadius = Radius(n); border.BorderThickness = new Thickness(n.Number("borderWidth")); Paint(border, Border.BackgroundProperty, n.Text("background")); Paint(border, Border.BorderBrushProperty, n.Text("border"));
            if (!n.Text("gradient").Equals("", StringComparison.Ordinal)) { var brush = new LinearGradientBrush { StartPoint = PointValue(n.Text("gradientStart")), EndPoint = PointValue(n.Text("gradientEnd")) }; var colors = n.Text("gradient").Split('|'); var offsets = n.Text("gradientOffsets").Split('|'); for (int i = 0; i < Math.Min(colors.Length, offsets.Length); i++) brush.GradientStops.Add(new GradientStop(((SolidColorBrush)Brush(colors[i], border)).Color, DesignContext.Number(offsets[i]))); border.Background = brush; }
            if (core is TextBlock t) { t.VerticalAlignment = n.Text("height") == "fill" && n.Text("align") != "top" ? VerticalAlignment.Center : VerticalAlignment.Stretch; t.FontSize = Math.Max(1, n.Number("fontSize")); t.FontWeight = Weight(n.Text("weight")); t.LineHeight = t.FontFamily.LineSpacing * t.FontSize; t.LineStackingStrategy = LineStackingStrategy.BlockLineHeight; t.MaxHeight = n.Props.ContainsKey("lines") ? t.LineHeight * Math.Max(1, n.Number("lines")) : double.PositiveInfinity; Paint(t, TextBlock.ForegroundProperty, n.Text("foreground")); AutomationProperties.SetAutomationId(t, n.Text("automation")); }
            AutomationProperties.SetName(border, n.Text("label")); ClipFrame(border, n);
            if (n.Props.ContainsKey("backgroundOpacity") && border.Background is Brush fill) { var clone = fill.CloneCurrentValue(); clone.Opacity *= Math.Clamp(n.Number("backgroundOpacity"), 0, 1); border.Background = clone; }
        });
        buildingViews[id] = new(node.Kind, core, border);
        return border;
    }
    private static void ClipFrame(Border border, DesignElement node)
    {
        border.Clip = node.Flag("clip") ? new RectangleGeometry(new Rect(border.RenderSize), border.CornerRadius.TopLeft, border.CornerRadius.TopLeft) : null;
    }
    private static void Detach(FrameworkElement? view)
    {
        if (view is null) return;
        if (VisualTreeHelper.GetParent(view) is Panel parent) parent.Children.Remove(view);
        else if (LogicalTreeHelper.GetParent(view) is ContentControl content && ReferenceEquals(content.Content, view)) content.Content = null;
        else if (LogicalTreeHelper.GetParent(view) is Border border && ReferenceEquals(border.Child, view)) border.Child = null;
    }
    private static void Reconcile(Panel parent, IReadOnlyList<UIElement> desired)
    {
        for (int i = parent.Children.Count - 1; i >= 0; i--) if (!desired.Contains(parent.Children[i])) parent.Children.RemoveAt(i);
        for (int i = 0; i < desired.Count; i++)
        {
            var child = desired[i]; if (i < parent.Children.Count && ReferenceEquals(parent.Children[i], child)) continue;
            if (child is FrameworkElement element) Detach(element);
            parent.Children.Insert(i, child);
        }
    }
    private static void Frame(FrameworkElement element, DesignElement node)
    {
        double Dimension(string key) => node.Props.ContainsKey(key) && node.Text(key) != "fill" ? Math.Max(0, node.Number(key)) : double.NaN;
        element.Width = Dimension("width"); element.Height = Dimension("height"); element.MinWidth = double.IsNaN(Dimension("minWidth")) ? 0 : Dimension("minWidth"); element.MinHeight = double.IsNaN(Dimension("minHeight")) ? 0 : Dimension("minHeight"); element.MaxWidth = double.IsNaN(Dimension("maxWidth")) ? double.PositiveInfinity : Dimension("maxWidth"); element.MaxHeight = double.IsNaN(Dimension("maxHeight")) ? double.PositiveInfinity : Dimension("maxHeight");
        element.HorizontalAlignment = node.Text("width") == "fill" || node.Kind is "column" or "row" or "overlay" or "scroll" or "slot" ? HorizontalAlignment.Stretch : node.Text("align") == "trailing" ? HorizontalAlignment.Right : node.Text("align") == "center" ? HorizontalAlignment.Center : HorizontalAlignment.Left;
        element.VerticalAlignment = node.Text("height") == "fill" || node.Kind is "column" or "row" or "overlay" or "scroll" or "slot" ? VerticalAlignment.Stretch : node.Text("align") == "top" ? VerticalAlignment.Top : VerticalAlignment.Center;
    }
    private static void Visual(Control control, DesignElement node)
    {
        control.FontSize = Math.Max(1, node.Number("fontSize")); control.FontWeight = Weight(node.Text("weight")); control.Padding = Insets(node); control.BorderThickness = new Thickness(node.Number("borderWidth")); Paint(control, Control.ForegroundProperty, node.Text("foreground")); Paint(control, Control.BackgroundProperty, node.Text("background")); Paint(control, Control.BorderBrushProperty, node.Text("border"));
    }
    private static Point PointValue(string source) { var values = source.Split(',').Select(DesignContext.Number).ToArray(); return new Point(values.ElementAtOrDefault(0), values.ElementAtOrDefault(1)); }
    private static Thickness Insets(DesignElement n) => new(n.Number("padding") + n.Number("paddingX"), n.Number("padding") + n.Number("paddingY"), n.Number("padding") + n.Number("paddingX"), n.Number("padding") + n.Number("paddingY"));
    private static CornerRadius Radius(DesignElement n) => new(n.Text("radius") == "circle" ? Math.Max(n.Number("width"), n.Number("height")) / 2 : n.Number("radius"));
    internal static FontWeight Weight(string name) => name switch { "heavy" => FontWeights.Heavy, "bold" => FontWeights.Bold, "semibold" => FontWeights.SemiBold, _ => FontWeights.Normal };
    private static void ResourceValues(Control control, DesignElement n)
    {
        control.Resources["design.focusThickness"] = new Thickness(n.Number("focusWidth"));
        control.Resources["design.scrollRadius"] = new CornerRadius(n.Number("thumbRadius"));
        if (control is ScrollViewer) control.Resources[typeof(System.Windows.Controls.Primitives.ScrollBar)] = control.FindResource("Design.ScrollBar");
        foreach (var pair in n.Props)
        {
            if (double.TryParse(pair.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var scalar) && double.IsFinite(scalar)) control.Resources["design." + pair.Key] = scalar;
            else if (pair.Value.StartsWith("theme.", StringComparison.Ordinal) || pair.Value.StartsWith('#') || pair.Value.StartsWith('@')) control.Resources["design." + pair.Key] = Brush(pair.Value, control);
        }
        // CornerRadius is a native value type, not the document scalar stored by the generic loop.
        control.Resources["design.radius"] = Radius(n);
        control.Resources["design.choiceGeometry"] = Geometry(n.Text("markName"), n.Number("markWidth"), n.Number("markHeight"));
        control.Resources["design.labelAutomation"] = n.Text("labelAutomation");
        control.Resources["design.choiceLabelMargin"] = new Thickness(n.Number("paddingX"), 0, n.Number("markWidth") + n.Number("markInset") * 2, 0);
        control.Resources["design.choiceMarkMargin"] = new Thickness(0, 0, n.Number("markInset"), 0);
        control.Resources["design.choiceRowPadding"] = new Thickness(n.Number("rowPaddingX"), n.Number("rowPaddingY"), n.Number("rowPaddingX"), n.Number("rowPaddingY"));
        control.Resources["design.thumbInsetThickness"] = new Thickness(n.Number("thumbInset"));
        control.Resources["design.trackRadius"] = new CornerRadius(n.Number("trackHeight") / 2);
    }
    internal static Brush Brush(string value, FrameworkElement? scope = null)
    {
        if (value.StartsWith("theme.", StringComparison.Ordinal) && (scope?.TryFindResource(value + ".brush") ?? Application.Current.TryFindResource(value + ".brush")) is Brush brush) return brush;
        if (value.StartsWith('@')) value = SyncWatchLayouts.Values.GetValueOrDefault(value[1..], "");
        if (value.Length == 9 && value[0] == '#' && uint.TryParse(value[1..], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var rgba)) return new SolidColorBrush(Color.FromArgb((byte)(rgba & 255), (byte)(rgba >> 24), (byte)(rgba >> 16 & 255), (byte)(rgba >> 8 & 255)));
        return Brushes.Transparent;
    }
    private static void Paint(FrameworkElement element, DependencyProperty property, string value)
    {
        if (value.StartsWith("theme.", StringComparison.Ordinal)) element.SetResourceReference(property, value + ".brush"); else element.SetValue(property, Brush(value, element));
    }
    private static Geometry Geometry(string name, double? width = null, double? height = null)
    {
        var result = new GeometryGroup { FillRule = FillRule.Nonzero };
        if (!SyncWatchLayouts.Assets.TryGetValue(name, out var polygons)) return result;
        foreach (var p in polygons) { var f = new PathFigure { StartPoint = new Point(p[0], p[1]), IsClosed = true, IsFilled = true }; for (int i = 2; i < p.Length; i += 2) f.Segments.Add(new LineSegment(new Point(p[i], p[i + 1]), true)); result.Children.Add(new PathGeometry([f])); }
        if (width is double w && height is double h) result.Transform = new ScaleTransform(w / SyncWatchLayouts.CoordinateSize, h / SyncWatchLayouts.CoordinateSize);
        return result;
    }
}

// Generic glyph-outline primitive. Position/typography/color rules are document properties.
public sealed class DesignSubtitle : FrameworkElement
{
    private DesignElement? node;
    private string language = "en";
    public void Update(DesignElement value, string locale) { node = value; language = locale; InvalidateVisual(); }
    protected override void OnRender(DrawingContext drawing)
    {
        base.OnRender(drawing); if (node is null) return;
        drawing.DrawRectangle(DesignSurface.Brush(node.Text("background")), null, new Rect(RenderSize));
        string family = node.Text("fontFamily") switch { "serif" => "Global Serif", "monospace" => "Global Monospace", _ => "Global User Interface" };
        var text = new FormattedText(node.Text("text"), CultureInfo.GetCultureInfo(language), FlowDirection.LeftToRight, new Typeface(new FontFamily(family), FontStyles.Normal, DesignSurface.Weight(node.Text("weight")), FontStretches.Normal), Math.Max(1, node.Number("fontSize")), DesignSurface.Brush(node.Text("foreground")), VisualTreeHelper.GetDpi(this).PixelsPerDip) { MaxTextWidth = Math.Max(1, ActualWidth - node.Number("textPaddingX") * 2), MaxLineCount = (int)node.Number("lines"), TextAlignment = TextAlignment.Center };
        var geometry = text.BuildGeometry(new Point()); geometry.Transform = new TranslateTransform((ActualWidth - geometry.Bounds.Width) / 2 - geometry.Bounds.X, node.Number("centerY") - geometry.Bounds.Height / 2 - geometry.Bounds.Y);
        drawing.DrawGeometry(DesignSurface.Brush(node.Text("foreground")), new Pen(DesignSurface.Brush(node.Text("outline")), node.Number("outlineWidth")), geometry);
    }
}
