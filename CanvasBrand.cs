using System;
using System.Drawing;
using System.Drawing.Drawing2D;

internal static class CanvasBrand
{
    internal static GraphicsPath Rounded(RectangleF rectangle, float radius)
    {
        var path = new GraphicsPath();
        float d = radius * 2;
        path.AddArc(rectangle.Left, rectangle.Top, d, d, 180, 90);
        path.AddArc(rectangle.Right - d, rectangle.Top, d, d, 270, 90);
        path.AddArc(rectangle.Right - d, rectangle.Bottom - d, d, d, 0, 90);
        path.AddArc(rectangle.Left, rectangle.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    private static float Clamp(float value) { return Math.Max(0, Math.Min(1, value)); }

    private static void Trace(Graphics graphics, GraphicsPath outline, Pen pen, float reveal)
    {
        if (reveal >= 1) { graphics.DrawPath(pen, outline); return; }
        if (reveal <= 0) return;
        using (var flat = (GraphicsPath)outline.Clone())
        {
            flat.Flatten(null, 0.25F);
            PointF[] points = flat.PathPoints;
            double length = 0;
            for (int i = 0; i < points.Length; i++) length += Distance(points[i], points[(i + 1) % points.Length]);
            double remaining = length * reveal;
            for (int i = 0; i < points.Length && remaining > 0; i++)
            {
                PointF start = points[i], end = points[(i + 1) % points.Length];
                double segment = Distance(start, end);
                if (segment <= 0) continue;
                float fraction = (float)Math.Min(1, remaining / segment);
                graphics.DrawLine(pen, start, new PointF(start.X + (end.X - start.X) * fraction, start.Y + (end.Y - start.Y) * fraction));
                remaining -= segment;
            }
        }
    }

    private static double Distance(PointF a, PointF b)
    {
        double x = a.X - b.X, y = a.Y - b.Y;
        return Math.Sqrt(x * x + y * y);
    }

    internal static void Draw(Graphics graphics, RectangleF bounds, float reveal, float sheen)
    {
        GraphicsState state = graphics.Save();
        try
        {
            float scale = Math.Min(bounds.Width, bounds.Height) / 256F;
            graphics.TranslateTransform(bounds.Left + (bounds.Width - 256 * scale) / 2, bounds.Top + (bounds.Height - 256 * scale) / 2);
            graphics.ScaleTransform(scale, scale);
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            var tile = new RectangleF(12, 12, 232, 232);
            using (var path = Rounded(tile, 60))
            using (var surface = new LinearGradientBrush(tile, Color.FromArgb(65, 66, 76), Color.FromArgb(17, 18, 24), 58F))
            using (var rim = new Pen(Color.FromArgb(155, 225, 228, 240), 1.4F))
            {
                graphics.FillPath(surface, path);
                graphics.DrawPath(rim, path);
                GraphicsState lightState = graphics.Save();
                graphics.SetClip(path, CombineMode.Intersect);
                using (var highlight = new LinearGradientBrush(new RectangleF(12, 12, 232, 124), Color.FromArgb(32, Color.White), Color.FromArgb(0, Color.White), 90F))
                    graphics.FillEllipse(highlight, -30, -55, 295, 205);
                if (sheen > 0 && sheen < 1)
                {
                    float x = -140 + sheen * 500;
                    using (var sweep = new LinearGradientBrush(new RectangleF(x, 12, 90, 232), Color.Transparent, Color.Transparent, 0F))
                    {
                        sweep.InterpolationColors = new ColorBlend { Colors = new[] { Color.FromArgb(0, Color.White), Color.FromArgb(23, Color.White), Color.FromArgb(0, Color.White) }, Positions = new[] { 0F, .5F, 1F } };
                        graphics.FillRectangle(sweep, x, 12, 90, 232);
                    }
                }
                graphics.Restore(lightState);
            }
            float backReveal = Clamp(reveal / .72F), frontReveal = Clamp((reveal - .22F) / .78F);
            using (var back = Rounded(new RectangleF(49, 61, 108, 91), 21))
            using (var fill = new SolidBrush(Color.FromArgb((int)(22 * backReveal), 240, 243, 255)))
            using (var stroke = new Pen(Color.FromArgb(223, 229, 238), 10F) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round })
            {
                graphics.FillPath(fill, back);
                Trace(graphics, back, stroke, backReveal);
            }
            if (frontReveal > 0)
            {
                var rectangle = new RectangleF(99, 111, 108, 91);
                using (var front = Rounded(rectangle, 21))
                using (var fill = new LinearGradientBrush(rectangle, Color.FromArgb((int)(255 * frontReveal), 67, 69, 80), Color.FromArgb((int)(255 * frontReveal), 34, 36, 44), 65F))
                using (var stroke = new Pen(Color.FromArgb(248, 249, 255), 10F) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round })
                {
                    graphics.FillPath(fill, front);
                    Trace(graphics, front, stroke, frontReveal);
                }
            }
        }
        finally { graphics.Restore(state); }
    }
}
