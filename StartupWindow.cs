using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.Runtime.InteropServices;
using System.Windows.Forms;

// Only a first-paint/error fallback. The canvas owns the visible transition.
internal sealed class StartupSurface : Control
{
    private readonly Button retry = new Button();
    private bool failed, finished;
    private string status = "正在准备创作空间…";
    internal bool HandedOff { get; private set; }
    internal event EventHandler Finished;
    internal event EventHandler RetryRequested;
    [DllImport("user32.dll")] private static extern bool ReleaseCapture();
    [DllImport("user32.dll")] private static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, IntPtr l);

    internal StartupSurface()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        BackColor = Color.FromArgb(233, 233, 237);
        AccessibleName = "映流启动画面";
        retry.Text = "重试"; retry.AccessibleName = "重新载入画布";
        retry.FlatStyle = FlatStyle.Flat; retry.FlatAppearance.BorderSize = 0;
        retry.BackColor = Color.FromArgb(47, 48, 58); retry.ForeColor = Color.White;
        retry.Font = new Font("Microsoft YaHei UI", 10F); retry.Visible = false;
        retry.Click += delegate {
            failed = false; HandedOff = false; retry.Visible = false; Report("正在重新载入画布…");
            var handler = RetryRequested; if (handler != null) handler(this, EventArgs.Empty);
        };
        Controls.Add(retry);
    }
    internal void SetBackground(Color color) { BackColor = color; Invalidate(); }
    internal void Report(string message)
    {
        if (IsDisposed || finished || failed) return;
        status = String.IsNullOrWhiteSpace(message) ? "正在准备创作空间…" : message;
        AccessibleDescription = status; Invalidate();
    }
    internal void HandOff()
    {
        if (!IsDisposed && !finished && !failed) { HandedOff = true; Visible = false; }
    }
    internal void ShowFailure(string message)
    {
        if (IsDisposed || finished) return;
        failed = true; HandedOff = false;
        status = String.IsNullOrWhiteSpace(message) ? "画布暂时无法打开，请重试。" : message;
        if (status.Length > 240) status = status.Substring(0, 240);
        AccessibleDescription = status;
        Visible = true; BringToFront(); retry.Visible = true; retry.BringToFront(); Invalidate();
    }
    internal void Complete()
    {
        if (IsDisposed || finished) return;
        finished = true; failed = false; retry.Visible = false; Visible = false;
        var handler = Finished; if (handler != null) handler(this, EventArgs.Empty);
    }
    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e); if (retry == null) return;
        float scale = DeviceDpi / 96F;
        retry.Bounds = new Rectangle((Width - (int)(100 * scale)) / 2, (int)(Height * .44F + 166 * scale), (int)(100 * scale), (int)(36 * scale));
        if (retry.Width <= 0 || retry.Height <= 0) return;
        using (var path = CanvasBrand.Rounded(new RectangleF(0, 0, retry.Width, retry.Height), 12 * scale))
        { Region old = retry.Region; retry.Region = new Region(path); if (old != null) old.Dispose(); }
    }
    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        var host = FindForm();
        if (e.Button == MouseButtons.Left && host != null) { ReleaseCapture(); SendMessage(host.Handle, 0xA1, new IntPtr(2), IntPtr.Zero); }
    }
    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e); DrawScene(e.Graphics, ClientSize, BackColor, status);
    }
    private static void DrawScene(Graphics graphics, Size size, Color background, string message)
    {
        bool contrast = SystemInformation.HighContrast;
        Color ink = contrast ? SystemColors.WindowText : background.GetBrightness() < .4F ? Color.FromArgb(236, 240, 247) : Color.FromArgb(34, 34, 37);
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        graphics.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        using (var fill = new SolidBrush(contrast ? SystemColors.Window : background)) graphics.FillRectangle(fill, 0, 0, size.Width, size.Height);
        float scale = graphics.DpiX / 96F, cx = size.Width / 2F, cy = size.Height * .44F;
        CanvasBrand.Draw(graphics, new RectangleF(cx - 56 * scale, cy - 56 * scale, 112 * scale, 112 * scale), 1, 0);
        using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
        using (var title = new Font("Microsoft YaHei UI", 26 * scale, FontStyle.Bold, GraphicsUnit.Pixel))
        using (var brush = new SolidBrush(ink))
            graphics.DrawString("映流", title, brush, new RectangleF(cx - 200 * scale, cy + 76 * scale, 400 * scale, 40 * scale), format);
        using (var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Near })
        using (var font = new Font("Microsoft YaHei UI", 14 * scale, FontStyle.Regular, GraphicsUnit.Pixel))
        using (var brush = new SolidBrush(ink))
            graphics.DrawString(message, font, brush, new RectangleF(cx - 270 * scale, cy + 126 * scale, 540 * scale, 40 * scale), format);
    }
    internal static void RenderPreview(string path, Size size, float seconds, bool reducedMotion)
    {
        using (var bitmap = new Bitmap(size.Width, size.Height, PixelFormat.Format32bppArgb))
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.Clear(Color.Transparent);
            using (var round = CanvasBrand.Rounded(new RectangleF(0, 0, size.Width, size.Height), 32)) graphics.SetClip(round);
            DrawScene(graphics, size, Color.FromArgb(233, 233, 237), "正在准备创作空间…");
            bitmap.Save(path, ImageFormat.Png);
        }
    }
}
