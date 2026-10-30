using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Management;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Launcher
{
    private static readonly string Root = Path.GetDirectoryName(typeof(Launcher).Assembly.Location);
    private static int Port = 8766;
    private const string RequiredVersion = "1.7.5";
    private static string Address { get { return "http://127.0.0.1:" + Port; } }
    private static volatile bool Cancelled;
    private static int recoveryRunning;

    private delegate bool EnumWindowCallback(IntPtr window, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int maximum);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowCallback callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int maximum);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)] private static extern IntPtr CommandLineToArgvW(string commandLine, out int count);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr memory);

    private static bool UsesCanvasProfile(string commandLine)
    {
        return UsesCanvasAddress(commandLine, null);
    }

    private static bool UsesCanvasAddress(string commandLine, string address)
    {
        if (String.IsNullOrEmpty(commandLine)) return false;
        int count;
        IntPtr arguments = CommandLineToArgvW(commandLine, out count);
        if (arguments == IntPtr.Zero) return false;
        try
        {
            string profile = null, app = null;
            for (int index = 0; index < count; index++)
            {
                string argument = Marshal.PtrToStringUni(Marshal.ReadIntPtr(arguments, index * IntPtr.Size));
                if (argument.StartsWith("--user-data-dir=", StringComparison.OrdinalIgnoreCase)) profile = argument.Substring(16);
                if (argument.StartsWith("--app=", StringComparison.OrdinalIgnoreCase)) app = argument.Substring(6);
            }
            string expected = Path.GetFullPath(Path.Combine(Root, "data", "browser-profile")).TrimEnd(Path.DirectorySeparatorChar);
            Uri appUri;
            bool canvasAddress = Uri.TryCreate(app, UriKind.Absolute, out appUri) && appUri.Scheme == "http" && appUri.Host == "127.0.0.1"
                && appUri.Port >= 8766 && appUri.Port <= 8774 && appUri.AbsolutePath == "/" && appUri.Query == "" && appUri.Fragment == "" && appUri.UserInfo == "";
            return profile != null && String.Equals(Path.GetFullPath(profile).TrimEnd(Path.DirectorySeparatorChar), expected, StringComparison.OrdinalIgnoreCase)
                && canvasAddress
                && (address == null || app == address);
        }
        catch (ArgumentException) { return false; }
        finally { LocalFree(arguments); }
    }

    private static HashSet<uint> CanvasBrowserProcesses()
    {
        return CanvasProcessesAtAddress(null);
    }

    private static HashSet<uint> CanvasProcessesAtAddress(string address)
    {
        var processes = new HashSet<uint>();
        try
        {
            using (var query = new ManagementObjectSearcher("SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name='msedge.exe'"))
            using (var results = query.Get())
                foreach (ManagementObject process in results)
                    using (process)
                        if (UsesCanvasAddress(process["CommandLine"] as string, address)) processes.Add(Convert.ToUInt32(process["ProcessId"]));
        }
        catch (ManagementException) { /* Keep opening the app if process metadata is unavailable. */ }
        catch (UnauthorizedAccessException) { }
        return processes;
    }

    private static List<IntPtr> CanvasWindows(HashSet<uint> processes)
    {
        return OwnedWindows(processes, false);
    }

    private static List<IntPtr> OwnedWindows(HashSet<uint> processes, bool includeErrorPage)
    {
        var windows = new List<IntPtr>();
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (!processes.Contains(processId)) return true;
            var title = new StringBuilder(256);
            GetWindowText(window, title, title.Capacity);
            var className = new StringBuilder(64);
            GetClassName(window, className, className.Capacity);
            if (title.ToString() == "映流" || title.ToString() == "创作画布" || (includeErrorPage && IsWindowVisible(window) && className.ToString() == "Chrome_WidgetWin_1")) windows.Add(window);
            return true;
        }, IntPtr.Zero);
        return windows;
    }

    private static bool RoundWindow(IntPtr window)
    {
        try
        {
            // Windows keeps its own frame, shadows, resize handles and maximized policy.
            int preference = 2; // DWMWCP_ROUND, Windows 11 build 22000+.
            return DwmSetWindowAttribute(window, 33, ref preference, sizeof(int)) == 0;
        }
        catch (DllNotFoundException) { return false; }
        catch (EntryPointNotFoundException) { return false; }
    }

    private static int ApplyCanvasWindowCorners(int waitMilliseconds)
    {
        var elapsed = Stopwatch.StartNew();
        var rounded = new HashSet<IntPtr>();
        var processes = CanvasBrowserProcesses();
        do
        {
            if (processes.Count == 0) processes = CanvasBrowserProcesses();
            foreach (IntPtr window in CanvasWindows(processes))
                if (!rounded.Contains(window) && RoundWindow(window)) rounded.Add(window);
            if (elapsed.ElapsedMilliseconds >= waitMilliseconds) break;
            Thread.Sleep(250);
        } while (true);
        return rounded.Count;
    }

    [DataContract]
    private sealed class ServiceReply
    {
        [DataMember(Name = "app")] public string App { get; set; }
        [DataMember(Name = "version")] public string Version { get; set; }
        [DataMember(Name = "startupProtocol")] public int StartupProtocol { get; set; }
        [DataMember(Name = "connected")] public bool Connected { get; set; }
        [DataMember(Name = "startup")] public StartupReply Startup { get; set; }
    }

    [DataContract]
    private sealed class StartupReply
    {
        [DataMember(Name = "status")] public string Status { get; set; }
        [DataMember(Name = "message")] public string Message { get; set; }
    }

    private static ServiceReply Api(string path, string method)
    {
        var request = (HttpWebRequest)WebRequest.Create(Address + path);
        request.Proxy = null;
        request.Timeout = 6000;
        request.ReadWriteTimeout = 6000;
        request.Method = method;
        if (method == "POST")
        {
            byte[] body = Encoding.UTF8.GetBytes("{}");
            request.ContentType = "application/json";
            request.ContentLength = body.Length;
            using (var stream = request.GetRequestStream()) stream.Write(body, 0, body.Length);
        }
        using (var response = request.GetResponse())
            return (ServiceReply)new DataContractJsonSerializer(typeof(ServiceReply)).ReadObject(response.GetResponseStream());
    }

    private static bool IsReady()
    {
        try
        {
            var response = Api("/api/health", "GET");
            return response.App == "creation-canvas" && response.StartupProtocol == 1 && response.Version == RequiredVersion;
        }
        catch (WebException) { return false; }
        catch (SerializationException) { return false; }
    }

    private static bool PortInUse(int port)
    {
        foreach (var endpoint in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
            if (endpoint.Port == port) return true;
        return false;
    }

    private static void SelectServicePort()
    {
        // Keep an existing window attached to its port even when its service is unavailable.
        for (int candidate = 8766; candidate <= 8774; candidate++)
        {
            Port = candidate;
            if (NativeWindowOpen()) return;
        }
        int available = 0;
        for (int candidate = 8766; candidate <= 8774; candidate++)
        {
            Port = candidate;
            if (IsReady()) return;
            if (available == 0 && !PortInUse(candidate)) available = candidate;
        }
        if (available == 0) throw new InvalidOperationException("画布服务端口均被占用，请关闭旧画布窗口后重试。");
        Port = available;
    }

    private static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(Path.Combine(Root, "data"));
            File.AppendAllText(Path.Combine(Root, "data", "launcher.log"), DateTime.Now.ToString("s") + " " + message + Environment.NewLine);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private static void EnsureService(bool cancelable)
    {
        using (var mutex = new Mutex(false, "Local\\CreationCanvasServiceStart"))
        {
            bool acquired;
            try { acquired = mutex.WaitOne(35000); }
            catch (AbandonedMutexException) { acquired = true; }
            if (!acquired) throw new InvalidOperationException("另一个窗口正在恢复本地服务，请稍后重试。");
            try { EnsureServiceCore(cancelable); }
            finally { mutex.ReleaseMutex(); }
        }
    }

    private static void EnsureServiceCore(bool cancelable)
    {
        // Never spawn over a listening process, even if its health request was slow.
        if (IsReady()) return;
        if (PortInUse(Port)) throw new InvalidOperationException("本地服务暂时无响应，保留现有进程等待恢复。端口：" + Port);
        var service = new ProcessStartInfo(FindPython(), "-X utf8 \"" + Path.Combine(Root, "run_app.py") + "\" --port " + Port);
        service.WorkingDirectory = Root;
        service.UseShellExecute = false;
        service.CreateNoWindow = true;
        service.WindowStyle = ProcessWindowStyle.Hidden;
        using (var process = Process.Start(service))
        {
            Log("正在启动画布服务 " + Address + "; PID=" + process.Id);
            var waiting = Stopwatch.StartNew();
            while ((!cancelable || !Cancelled) && waiting.Elapsed.TotalSeconds < 30)
            {
                if (IsReady()) return;
                if (process.HasExited) break;
                Thread.Sleep(250);
            }
        }
        if (!cancelable || !Cancelled) throw new InvalidOperationException("映流服务未能启动。请查看 data/application.log，确认 " + Port + " 端口可用。");
    }

    internal static void RecoverService()
    {
        if (Interlocked.Exchange(ref recoveryRunning, 1) != 0) return;
        try
        {
            EnsureService(false);
            Api("/api/comfy/start", "POST");
        }
        catch (Exception error) { Log("恢复服务失败：" + error.Message); }
        finally { Interlocked.Exchange(ref recoveryRunning, 0); }
    }

    private static bool ActivateWindow()
    {
        bool activated = false;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            if (CanvasNative.GetProp(window, WindowProperty(Port)) == IntPtr.Zero) return true;
            if (IsIconic(window)) ShowWindow(window, 9);
            SetForegroundWindow(window);
            activated = true;
            return false;
        }, IntPtr.Zero);
        return activated;
    }

    private static bool NativeWindowOpen()
    {
        bool found = false;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            if (CanvasNative.GetProp(window, WindowProperty(Port)) != IntPtr.Zero) { found = true; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    internal static string WindowProperty(int port)
    {
        return "CreationCanvas.NativeWindow-" + port + "-" + RequiredVersion;
    }

    private static bool ShouldRecover(int failures, bool listening, double secondsSinceAttempt)
    {
        return failures >= 2 && !listening && secondsSinceAttempt >= 30;
    }

    private static void GuardService()
    {
        bool acquired;
        using (var mutex = new Mutex(true, "Local\\CreationCanvasNativeServiceGuard-" + RequiredVersion, out acquired))
        {
            if (!acquired) return;
            try
            {
                int failures = 0, absent = 0;
                var lastAttempt = Stopwatch.StartNew();
                bool attempted = false;
                while (true)
                {
                    // Include the owned app's error page: a changed title must not disable recovery.
                    if (!NativeWindowOpen())
                    {
                        if (++absent >= 3) return;
                    }
                    else
                    {
                        absent = 0;
                        if (IsReady()) failures = 0;
                        else if (ShouldRecover(++failures, PortInUse(Port), attempted ? lastAttempt.Elapsed.TotalSeconds : 30))
                        {
                            attempted = true;
                            lastAttempt.Restart();
                            try { EnsureService(false); failures = 0; Log("画布服务已恢复 " + Address); }
                            catch (Exception error) { Log("恢复画布服务失败：" + error.Message); }
                        }
                    }
                    Thread.Sleep(5000);
                }
            }
            finally { mutex.ReleaseMutex(); }
        }
    }

    private static void WaitForEngine(Action<string> report)
    {
        // The canvas service may already exist while ComfyUI has been closed.
        Api("/api/comfy/start", "POST");
        var elapsed = Stopwatch.StartNew();
        while (!Cancelled && elapsed.Elapsed.TotalSeconds < 190)
        {
            ServiceReply status;
            try { status = Api("/api/comfy/status", "GET"); }
            catch (WebException)
            {
                report("正在等待本地服务恢复连接…");
                Thread.Sleep(800);
                continue;
            }
            if (status.Connected) return;
            if (status.Startup != null && status.Startup.Status == "error")
                throw new InvalidOperationException(status.Startup.Message);
            report("正在启动本地 ComfyUI… 已等待 " + (int)elapsed.Elapsed.TotalSeconds + " 秒\n首次启动需要加载节点，请稍候。");
            Thread.Sleep(800);
        }
        if (!Cancelled) throw new InvalidOperationException("等待 ComfyUI 就绪超时，请在画布右上角查看连接详情并重试。");
    }

    private static void PrepareCanvas(Action<string> report)
    {
        report("正在准备本地画布服务…");
        EnsureService(true);
        if (Cancelled) return;
        report("正在连接本地创作引擎…");
        try { Api("/api/comfy/start", "POST"); }
        catch (Exception error)
        {
            // Editing can open while the engine starts or its connection recovers.
            Log(error.Message);
        }
    }

    private static System.Reflection.Assembly ResolveDesktopAssembly(object sender, ResolveEventArgs args)
    {
        string name = new System.Reflection.AssemblyName(args.Name).Name;
        if (name != "Microsoft.Web.WebView2.Core" && name != "Microsoft.Web.WebView2.WinForms") return null;
        string path = Path.Combine(Root, "data", "desktop-shell-bin", name + ".dll");
        return File.Exists(path) ? System.Reflection.Assembly.LoadFrom(path) : null;
    }

    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    private static void RunWindow(string address, bool diagnostic)
    {
        RunWindow(address, diagnostic, null);
    }

    [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
    private static void RunWindow(string address, bool diagnostic, Action<Action<string>> prepare)
    {
        string loader = Path.Combine(Root, "data", "desktop-shell-bin", "WebView2Loader.dll");
        if (CanvasNative.LoadLibraryEx(loader, IntPtr.Zero, 0x1100) == IntPtr.Zero)
            throw new IOException("无法载入桌面窗口组件，请重新运行构建脚本。错误码：" + Marshal.GetLastWin32Error());
        using (var window = new CanvasWindow(Root, address, diagnostic, prepare))
        {
            if (prepare != null) window.FormClosed += delegate { Cancelled = true; };
            if (!diagnostic && !address.Contains(":8775")) new Thread(GuardService) { IsBackground = true }.Start();
            Application.Run(window);
            if (window.Failed) Environment.ExitCode = 1;
        }
    }

    private static string FindPython()
    {
        string configured = Environment.GetEnvironmentVariable("YINGLIU_PYTHON");
        foreach (string file in new[] { Path.Combine(Root, "config.json"), Path.Combine(Root, "data", "settings.json") })
        {
            if (!File.Exists(file)) continue;
            var values = new System.Web.Script.Serialization.JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(file, Encoding.UTF8).TrimStart('\uFEFF'));
            object value;
            if (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("YINGLIU_PYTHON")) && values != null && values.TryGetValue("app_python", out value) && value is string && !String.IsNullOrWhiteSpace((string)value)) configured = (string)value;
        }
        if (!String.IsNullOrWhiteSpace(configured))
        {
            configured = Environment.ExpandEnvironmentVariables(configured);
            if (!Path.IsPathRooted(configured)) configured = Path.Combine(Root, configured);
            if (File.Exists(configured)) return configured;
            throw new InvalidOperationException("设置的画布 Python 不存在，请检查 config.json 中的 app_python。");
        }
        var candidates = new List<string> {
            Path.Combine(Root, @".venv\Scripts\python.exe"),
            Path.Combine(Root, @"runtime\python\python.exe")
        };
        foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            if (String.IsNullOrWhiteSpace(directory)) continue;
            try { candidates.Add(Path.Combine(directory.Trim('"'), "python.exe")); }
            catch (ArgumentException) { }
        }
        foreach (string candidate in candidates) if (File.Exists(candidate)) return candidate;
        throw new InvalidOperationException("未找到 Python 3.10 或以上版本。请先运行 setup.ps1，或在 config.json 中填写 app_python；也可以用 Python 直接启动 server.py。");
    }

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolveDesktopAssembly;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length == 1 && args[0] == "--check-window") { RunWindow("http://127.0.0.1:8775", true); return; }
            if (args.Length == 1 && args[0] == "--preview-url=http://127.0.0.1:8775/") { RunWindow("http://127.0.0.1:8775", false); return; }
            bool acquired;
            using (var mutex = new Mutex(true, "Local\\CreationCanvasNativeLauncher-" + RequiredVersion, out acquired))
            {
                if (!acquired) { SelectServicePort(); if (ActivateWindow()) RecoverService(); return; }
                SelectServicePort();
                Cancelled = false;
                if (!ActivateWindow()) RunWindow(Address, false, PrepareCanvas);
                else RecoverService();
                mutex.ReleaseMutex();
            }
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "映流", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
    }
}
