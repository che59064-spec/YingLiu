using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class CanvasWindow : Form
{
    private const int Rim = 5;
    private readonly string root;
    private readonly Uri page;
    private readonly bool diagnostic;
    private readonly WebView2 browser;
    private readonly JavaScriptSerializer json = new JavaScriptSerializer();
    private bool closing, updatingShape;
    private int shapeDepth, maximumShapeDepth, appearanceMessages, windowMessages;
    private int cornerResult;
    private readonly Action<Action<string>> prepareStartup;
    private StartupSurface startup;
    private int startupAttempt;
    private bool browserEventsReady, startupFailed, startupReady;
    internal bool Failed { get; private set; }

    internal CanvasWindow(string workspace, string address, bool check)
        : this(workspace, address, check, null) { }

    internal CanvasWindow(string workspace, string address, bool check, Action<Action<string>> prepare)
    {
        root = workspace;
        page = new Uri(address.TrimEnd('/') + "/");
        diagnostic = check;
        prepareStartup = prepare;
        Text = "映流";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.FromArgb(233, 233, 237);
        try {
            string color = File.ReadAllText(Path.Combine(root, "data", "startup-color.txt")).Trim();
            if (StartupColor(color)) BackColor = ColorTranslator.FromHtml(color);
        } catch (IOException) { } catch (UnauthorizedAccessException) { }
        MinimumSize = new Size(840, 580);
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        Size = new Size(Math.Min(1500, area.Width - 60), Math.Min(980, area.Height - 60));
        Padding = new Padding(Rim);
        browser = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor };
        Controls.Add(browser);
        if (prepare != null && !check)
        {
            startup = new StartupSurface { Dock = DockStyle.Fill };
            startup.SetBackground(BackColor);
            startup.Finished += delegate {
                if (closing || IsDisposed) return;
                var completed = startup; startup = null;
                if (completed != null) { Controls.Remove(completed); completed.Dispose(); }
                browser.Focus();
            };
            startup.RetryRequested += async delegate { await StartWithSurfaceAsync(); };
            Controls.Add(startup); startup.BringToFront();
        }
        Shown += async delegate { if (startup != null) await StartWithSurfaceAsync(); else await InitializeAsync(); };
        if (diagnostic)
        {
            ShowInTaskbar = false;
            Opacity = 0;
            StartPosition = FormStartPosition.Manual;
            Location = new Point(-20000, -20000);
        }
    }

    private async Task StartWithSurfaceAsync()
    {
        int attempt = ++startupAttempt;
        startupFailed = false; startupReady = false;
        try
        {
            await Task.Run(delegate {
                prepareStartup(delegate(string message) {
                    if (closing || IsDisposed || !IsHandleCreated) return;
                    try { BeginInvoke((Action)delegate { if (attempt == startupAttempt && startup != null) startup.Report(message); }); }
                    catch (InvalidOperationException) { }
                });
            });
            if (closing || IsDisposed || attempt != startupAttempt) return;
            if (startup != null) startup.Report("正在读取你的画布…");
            await InitializeAsync();
            await Task.Delay(30000);
            while (!closing && !IsDisposed && attempt == startupAttempt && WindowState == FormWindowState.Minimized) await Task.Delay(500);
            if (!closing && !IsDisposed && attempt == startupAttempt && startup != null && !startupFailed && !startupReady)
                Fail(new TimeoutException("画布页面未能及时打开，请重试；原画布内容仍保存在本机。"));
        }
        catch (Exception error) { if (attempt == startupAttempt && !closing && !IsDisposed) Fail(error); }
    }

    private async Task WaitForCanvasAsync(int attempt)
    {
        for (int i = 0; i < 80;)
        {
            if (closing || IsDisposed || attempt != startupAttempt || startup == null || startupFailed || startupReady) return;
            if (!CurrentStartupSource(browser.CoreWebView2.Source)) return;
            if (WindowState == FormWindowState.Minimized) { await Task.Delay(500); continue; }
            i++;
            try
            {
                string ready = await browser.CoreWebView2.ExecuteScriptAsync("Boolean(window.CanvasApp && window.CanvasApp.state && window.CanvasApp.state.booted)");
                if (ready == "true") {
                    await browser.CoreWebView2.ExecuteScriptAsync("window.CanvasStartup && window.CanvasStartup.notify('ready')");
                    if (attempt == startupAttempt && startup != null) { startupReady = true; Failed = false; startup.Complete(); } return;
                }
            }
            catch (Exception error) { if (i == 79) Log(error); }
            await Task.Delay(250);
        }
        if (!closing && !IsDisposed && attempt == startupAttempt && startup != null && !startupFailed && !startupReady)
            Fail(new TimeoutException("画布读取超时，请重试；已保存内容仍在本机。"));
    }

    protected override bool ShowWithoutActivation { get { return diagnostic; } }

    protected override CreateParams CreateParams
    {
        get
        {
            CreateParams value = base.CreateParams;
            value.Style |= 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000;
            value.Style &= ~0x00C00000;
            if (diagnostic) value.ExStyle |= 0x08000080;
            return value;
        }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        CanvasNative.SetProp(Handle, Launcher.WindowProperty(page.Port), new IntPtr(1));
        UpdateFrame();
    }

    private void UpdateFrame()
    {
        if (closing || IsDisposed || Disposing || !IsHandleCreated) return;
        int corner = WindowState == FormWindowState.Maximized || IsDockedToWorkArea() ? 1 : 2;
        cornerResult = CanvasNative.DwmSetWindowAttribute(Handle, 33, ref corner, sizeof(int));
        int border = unchecked((int)0xFFFFFFFE);
        CanvasNative.DwmSetWindowAttribute(Handle, 34, ref border, sizeof(int));
        var margins = new CanvasNative.Margins { Left = 1, Right = 1, Top = 1, Bottom = 1 };
        CanvasNative.DwmExtendFrameIntoClientArea(Handle, ref margins);
        UpdateShape();
    }

    private static Region RoundedRegion(int width, int height, int radius)
    {
        int diameter = Math.Max(1, Math.Min(radius * 2, Math.Min(width, height)));
        using (var path = new GraphicsPath())
        {
            path.AddArc(0, 0, diameter, diameter, 180, 90);
            path.AddArc(width - diameter, 0, diameter, diameter, 270, 90);
            path.AddArc(width - diameter, height - diameter, diameter, diameter, 0, 90);
            path.AddArc(0, height - diameter, diameter, diameter, 90, 90);
            path.CloseFigure();
            return new Region(path);
        }
    }

    private void UpdateShape()
    {
        if (closing || updatingShape || IsDisposed || !IsHandleCreated || ClientSize.Width <= 0 || ClientSize.Height <= 0) return;
        updatingShape = true;
        shapeDepth++;
        maximumShapeDepth = Math.Max(maximumShapeDepth, shapeDepth);
        try
        {
            if (WindowState == FormWindowState.Maximized || IsDockedToWorkArea())
            {
                Region = null;
                if (browser != null && !browser.IsDisposed) browser.Region = null;
                return;
            }
            int radius = Math.Max(16, 32 * DeviceDpi / 96);
            Region = RoundedRegion(ClientSize.Width, ClientSize.Height, radius);
            if (browser != null && !browser.IsDisposed && browser.Width > 0 && browser.Height > 0)
                browser.Region = RoundedRegion(browser.Width, browser.Height, Math.Max(8, radius - Rim));
        }
        finally { shapeDepth--; updatingShape = false; }
    }

    private async Task InitializeAsync()
    {
        int attempt = startupAttempt;
        try
        {
            if (!browserEventsReady)
            {
            string binary = Path.Combine(root, "data", "desktop-shell-bin", "WebView2Loader.dll");
            if (CanvasNative.LoadLibraryEx(binary, IntPtr.Zero, 0x1100) == IntPtr.Zero)
                throw new IOException("WebView2 运行组件无法载入，错误码 " + Marshal.GetLastWin32Error());
            string profile = Path.Combine(root, "data", diagnostic ? "desktop-window-check-profile" : "webview-profile");
            var environmentTask = CoreWebView2Environment.CreateAsync(null, profile);
            if (await Task.WhenAny(environmentTask, Task.Delay(30000)) != environmentTask) throw new TimeoutException("WebView2 初始化超时");
            var initialization = browser.EnsureCoreWebView2Async(await environmentTask);
            if (await Task.WhenAny(initialization, Task.Delay(30000)) != initialization) throw new TimeoutException("WebView2 窗口初始化超时");
            await initialization;
            if (closing || IsDisposed || attempt != startupAttempt) return;
            browser.CoreWebView2.Settings.IsStatusBarEnabled = false;
            browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            browser.CoreWebView2.Settings.IsWebMessageEnabled = true;
            browser.CoreWebView2.Settings.AreDevToolsEnabled = diagnostic;
            browser.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) {
                if (TrustedSource(e.Uri)) return;
                e.Cancel = true;
                if (e.IsUserInitiated && !diagnostic) OpenExternal(e.Uri);
            };
            browser.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) {
                e.Handled = true;
                if (e.IsUserInitiated && !diagnostic) OpenExternal(e.Uri);
            };
            browser.CoreWebView2.WebMessageReceived += ReceiveMessage;
            browser.CoreWebView2.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e) {
                if (!e.IsSuccess)
                {
                    if (e.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled) Fail(new IOException("画布页面未能载入：" + e.WebErrorStatus));
                    return;
                }
                SendWindowState();
                if (diagnostic) await CheckWindowAsync();
                else if (startup != null) await WaitForCanvasAsync(startupAttempt);
            };
            browserEventsReady = true;
            }
            if (closing || IsDisposed || attempt != startupAttempt) return;
            if (startup != null) startup.BringToFront();
            browser.CoreWebView2.Navigate(page.AbsoluteUri + (startup != null ? "?canvas_startup=" + attempt : ""));
        }
        catch (Exception error) { if (!closing && !IsDisposed && attempt == startupAttempt) Fail(error); }
    }

    private bool TrustedSource(string value)
    {
        Uri source;
        return Uri.TryCreate(value, UriKind.Absolute, out source) && source.Scheme == page.Scheme &&
            source.Host == page.Host && source.Port == page.Port && source.UserInfo == "" && source.AbsolutePath == "/";
    }

    private void OpenExternal(string value)
    {
        Uri source;
        if (!Uri.TryCreate(value, UriKind.Absolute, out source) || source.UserInfo != "" || (source.Scheme != "http" && source.Scheme != "https")) return;
        try { Process.Start(new ProcessStartInfo(source.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception error) { Log(error); }
    }

    private void ReceiveMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (closing || !TrustedSource(e.Source) || !TrustedSource(browser.CoreWebView2.Source)) return;
        Dictionary<string, object> message;
        try { message = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); }
        catch (InvalidOperationException) { return; }
        catch (ArgumentException) { return; }
        if (message == null) return;
        string startupState = StartupSignal(message);
        if (startupState != null)
        {
            if (startup == null || startupReady || !CurrentStartupSource(e.Source)) return;
            if (startupFailed && startupState != "error") return;
            if (startupState == "painted") {
                if (startupFailed) return;
                object background;
                if (message.TryGetValue("background", out background)) {
                    BackColor = ColorTranslator.FromHtml((string)background); browser.DefaultBackgroundColor = BackColor;
                    startup.SetBackground(BackColor);
                    try { File.WriteAllText(Path.Combine(root, "data", "startup-color.txt"), (string)background); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                }
                startup.HandOff();
            }
            else if (startupState == "ready") { startupReady = true; startupFailed = false; Failed = false; startup.Complete(); }
            else { object detail; startupFailed = true; Failed = true; startup.ShowFailure(message.TryGetValue("message", out detail) ? (string)detail : "画布暂时无法载入，请重试。"); }
            return;
        }
        if (message.Count != 2) return;
        object type, value;
        if (!message.TryGetValue("type", out type) || !(type is string)) return;
        if ((string)type == "service" && message.TryGetValue("action", out value) && Object.Equals(value, "recover"))
        {
            if (!diagnostic) Task.Run((Action)Launcher.RecoverService);
            return;
        }
        if ((string)type == "appearance" && message.TryGetValue("theme", out value) && value is string)
        {
            if ((string)value != "dark" && (string)value != "light") return;
            int dark = (string)value == "dark" ? 1 : 0;
            if (startup == null) BackColor = dark == 1 ? Color.FromArgb(20, 29, 43) : Color.FromArgb(233, 233, 237);
            browser.DefaultBackgroundColor = BackColor;
            CanvasNative.DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int));
            appearanceMessages++;
        }
        else if ((string)type == "window" && message.TryGetValue("action", out value) && value is string)
        {
            string action = (string)value;
            if (action != "close" && action != "minimize" && action != "maximize" && action != "drag") return;
            windowMessages++;
            if (diagnostic) return;
            if (action == "close") Close();
            else if (action == "minimize") WindowState = FormWindowState.Minimized;
            else if (action == "maximize") ToggleMaximized();
            else
            {
                CanvasNative.ReleaseCapture();
                CanvasNative.SendMessage(Handle, 0x00A1, new IntPtr(2), IntPtr.Zero);
            }
            SendWindowState();
        }
    }

    internal static string StartupSignal(Dictionary<string, object> message)
    {
        object type, state, detail;
        if (message == null || message.Count < 2 || message.Count > 3 ||
            !message.TryGetValue("type", out type) || !Object.Equals(type, "startup") ||
            !message.TryGetValue("state", out state) || !(state is string)) return null;
        if (Object.Equals(state, "ready")) return message.Count == 2 ? "ready" : null;
        if (Object.Equals(state, "error")) return message.Count == 2 || (message.TryGetValue("message", out detail) && detail is string) ? "error" : null;
        if (Object.Equals(state, "painted")) return message.Count == 2 || (message.TryGetValue("background", out detail) && detail is string && StartupColor((string)detail)) ? "painted" : null;
        return null;
    }

    private static bool StartupColor(string value)
    {
        if (value == null || value.Length != 7 || value[0] != '#') return false;
        for (int i = 1; i < value.Length; i++) if (!Uri.IsHexDigit(value[i])) return false;
        return true;
    }

    private bool CurrentStartupSource(string source)
    {
        Uri parsed;
        return Uri.TryCreate(source, UriKind.Absolute, out parsed) && TrustedSource(source) &&
            parsed.Query == "?canvas_startup=" + startupAttempt;
    }

    protected override bool ProcessCmdKey(ref Message message, Keys keyData)
    {
        if (startup != null && keyData == Keys.Escape) { Close(); return true; }
        return base.ProcessCmdKey(ref message, keyData);
    }

    private void ToggleMaximized()
    {
        WindowState = WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
    }

    private bool IsDockedToWorkArea()
    {
        if (!IsHandleCreated || WindowState != FormWindowState.Normal) return false;
        Rectangle area = Screen.FromHandle(Handle).WorkingArea;
        int tolerance = Math.Max(2, DeviceDpi / 48);
        bool side = Math.Abs(Bounds.Left - area.Left) <= tolerance || Math.Abs(Bounds.Right - area.Right) <= tolerance;
        bool top = Math.Abs(Bounds.Top - area.Top) <= tolerance;
        bool bottom = Math.Abs(Bounds.Bottom - area.Bottom) <= tolerance;
        bool fullHeight = Math.Abs(Bounds.Height - area.Height) <= tolerance;
        bool halfHeight = Math.Abs(Bounds.Height * 2 - area.Height) <= tolerance * 2;
        return side && (fullHeight || (halfHeight && (top || bottom))) && Bounds.Width < area.Width;
    }

    private void SendWindowState()
    {
        if (closing || browser == null || browser.IsDisposed || browser.CoreWebView2 == null || !TrustedSource(browser.CoreWebView2.Source)) return;
        browser.CoreWebView2.PostWebMessageAsJson(json.Serialize(new { type = "window-state", maximized = WindowState == FormWindowState.Maximized }));
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        if (closing || IsDisposed || browser == null || browser.IsDisposed) return;
        Padding = WindowState == FormWindowState.Maximized ? Padding.Empty : new Padding(Rim);
        UpdateShape();
        SendWindowState();
    }

    protected override void OnLocationChanged(EventArgs e)
    {
        base.OnLocationChanged(e);
        if (!closing && browser != null) UpdateFrame();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        closing = true;
        base.OnFormClosing(e);
    }

    protected override void WndProc(ref Message message)
    {
        if (message.Msg == 0x0083 && message.WParam != IntPtr.Zero)
        {
            message.Result = IntPtr.Zero;
            return;
        }
        if (message.Msg == 0x02E0)
        {
            var rectangle = (CanvasNative.Rect)Marshal.PtrToStructure(message.LParam, typeof(CanvasNative.Rect));
            SetBounds(rectangle.Left, rectangle.Top, rectangle.Right - rectangle.Left, rectangle.Bottom - rectangle.Top);
            UpdateShape();
            message.Result = IntPtr.Zero;
            return;
        }
        if (message.Msg == 0x0024)
        {
            var bounds = (CanvasNative.MinMaxInfo)Marshal.PtrToStructure(message.LParam, typeof(CanvasNative.MinMaxInfo));
            var monitor = new CanvasNative.MonitorInfo { Size = Marshal.SizeOf(typeof(CanvasNative.MonitorInfo)) };
            if (CanvasNative.GetMonitorInfo(CanvasNative.MonitorFromWindow(Handle, 2), ref monitor))
            {
                bounds.MaxPosition.X = monitor.Work.Left - monitor.Monitor.Left;
                bounds.MaxPosition.Y = monitor.Work.Top - monitor.Monitor.Top;
                bounds.MaxSize.X = monitor.Work.Right - monitor.Work.Left;
                bounds.MaxSize.Y = monitor.Work.Bottom - monitor.Work.Top;
                bounds.MinTrackSize.X = MinimumSize.Width;
                bounds.MinTrackSize.Y = MinimumSize.Height;
                Marshal.StructureToPtr(bounds, message.LParam, false);
            }
            message.Result = IntPtr.Zero;
            return;
        }
        if (message.Msg == 0x0084 && WindowState == FormWindowState.Normal)
        {
            long location = message.LParam.ToInt64();
            Point point = PointToClient(new Point((short)(location & 0xffff), (short)((location >> 16) & 0xffff)));
            bool left = point.X < Rim, right = point.X >= ClientSize.Width - Rim;
            bool top = point.Y < Rim, bottom = point.Y >= ClientSize.Height - Rim;
            message.Result = new IntPtr(top && left ? 13 : top && right ? 14 : bottom && left ? 16 : bottom && right ? 17 : left ? 10 : right ? 11 : top ? 12 : bottom ? 15 : 1);
            return;
        }
        base.WndProc(ref message);
    }

    private async Task CheckWindowAsync()
    {
        try
        {
            await Task.Delay(600);
            int style = CanvasNative.GetWindowLong(Handle, -16);
            bool cornerExcluded = Region != null && !Region.IsVisible(0, 0);
            bool centerIncluded = Region != null && Region.IsVisible(ClientSize.Width / 2, ClientSize.Height / 2);
            string dom = await browser.CoreWebView2.ExecuteScriptAsync("JSON.stringify({desktop:document.documentElement.dataset.desktop,buttons:[...document.querySelectorAll('[data-window-action]')].map(b=>({action:b.dataset.windowAction,label:b.getAttribute('aria-label')})),controlsHidden:document.querySelector('#window-controls')?.hidden})");
            var domValues = json.Deserialize<Dictionary<string, object>>(json.Deserialize<string>(dom));
            bool controlsPresent = domValues.ContainsKey("desktop") && (string)domValues["desktop"] == "true" &&
                domValues.ContainsKey("controlsHidden") && domValues["controlsHidden"] is bool && !(bool)domValues["controlsHidden"] &&
                domValues["buttons"] is System.Collections.IList && ((System.Collections.IList)domValues["buttons"]).Count == 3;
            await browser.CoreWebView2.ExecuteScriptAsync("chrome.webview.postMessage({type:'window',action:'minimize'});chrome.webview.postMessage({type:'window',action:'maximize'});chrome.webview.postMessage({type:'window',action:'not-allowed'});chrome.webview.postMessage({type:'appearance',theme:'dark'});chrome.webview.postMessage({type:'appearance',theme:'light'});");
            await Task.Delay(100);
            ToggleMaximized();
            await Task.Delay(100);
            bool maxRectangular = Region == null;
            Rectangle expected = Screen.FromHandle(Handle).WorkingArea;
            bool maxWithinWorkArea = Bounds.Width == expected.Width && Bounds.Height == expected.Height;
            ToggleMaximized();
            await Task.Delay(100);
            bool restoredRounded = Region != null && !Region.IsVisible(0, 0);
            var receipt = new { success = (style & 0x00C00000) == 0 && cornerExcluded && centerIncluded && maxRectangular && restoredRounded && controlsPresent && windowMessages == 2 && appearanceMessages >= 2,
                captionRemoved = (style & 0x00C00000) == 0, resizable = (style & 0x00040000) != 0,
                cornerExcluded = cornerExcluded, centerIncluded = centerIncluded, normalRadiusLogical = 32,
                maximizedRectangular = maxRectangular, maximizedWithinWorkArea = maxWithinWorkArea, restoredRounded = restoredRounded,
                controlsPresent = controlsPresent, maximumShapeDepth = maximumShapeDepth, cornerResult = cornerResult, dpi = DeviceDpi,
                rejectsExternalOrigin = !TrustedSource("https://example.com/"), rejectsOtherLocalPort = !TrustedSource("http://127.0.0.1:8188/"),
                appearanceMessages = appearanceMessages, windowMessages = windowMessages, dom = dom, previewOnly = true, url = page.AbsoluteUri };
            string destination = Path.Combine(root, "qa", "desktop-window-check.json");
            File.WriteAllText(destination, json.Serialize(receipt), System.Text.Encoding.UTF8);
            Failed = !receipt.success;
            Close();
        }
        catch (Exception error) { Fail(error); }
    }

    private void Log(Exception error)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(root, "data"));
            File.AppendAllText(Path.Combine(root, "data", "desktop-window.log"), DateTime.Now.ToString("s") + " " + error + Environment.NewLine);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void Fail(Exception error)
    {
        Failed = true;
        Log(error);
        if (startup != null && !startup.IsDisposed) { startupFailed = true; startup.ShowFailure(error.Message); return; }
        if (!diagnostic) MessageBox.Show(this, "画布窗口未能打开。请查看 data/desktop-window.log。\n\n" + error.Message, "映流", MessageBoxButtons.OK, MessageBoxIcon.Information);
        Close();
    }
}

internal static class CanvasNative
{
    [StructLayout(LayoutKind.Sequential)] internal struct Point { internal int X, Y; }
    [StructLayout(LayoutKind.Sequential)] internal struct Rect { internal int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] internal struct Margins { internal int Left, Right, Top, Bottom; }
    [StructLayout(LayoutKind.Sequential)] internal struct MinMaxInfo { internal Point Reserved, MaxSize, MaxPosition, MinTrackSize, MaxTrackSize; }
    [StructLayout(LayoutKind.Sequential)] internal struct MonitorInfo { internal int Size; internal Rect Monitor, Work; internal uint Flags; }
    [DllImport("user32.dll")] internal static extern bool ReleaseCapture();
    [DllImport("user32.dll")] internal static extern IntPtr SendMessage(IntPtr handle, int message, IntPtr wparam, IntPtr lparam);
    [DllImport("user32.dll")] internal static extern IntPtr MonitorFromWindow(IntPtr handle, int flags);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] internal static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll")] internal static extern int GetWindowLong(IntPtr handle, int index);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] internal static extern bool SetProp(IntPtr handle, string name, IntPtr value);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] internal static extern IntPtr GetProp(IntPtr handle, string name);
    [DllImport("dwmapi.dll")] internal static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);
    [DllImport("dwmapi.dll")] internal static extern int DwmExtendFrameIntoClientArea(IntPtr handle, ref Margins margins);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] internal static extern IntPtr LoadLibraryEx(string path, IntPtr reserved, int flags);
}
