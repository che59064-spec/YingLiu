using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;

internal static class BuildBrandAssets
{
    private static byte[] Render(int size)
    {
        using (var bitmap = new Bitmap(size, size, PixelFormat.Format32bppArgb))
        using (var graphics = Graphics.FromImage(bitmap))
        using (var stream = new MemoryStream())
        {
            graphics.Clear(Color.Transparent);
            CanvasBrand.Draw(graphics, new RectangleF(0, 0, size, size), 1, 0);
            bitmap.Save(stream, ImageFormat.Png);
            return stream.ToArray();
        }
    }

    private static int Main(string[] args)
    {
        try
        {
            string root = Path.GetFullPath(args[0]);
            int[] sizes = { 16, 24, 32, 48, 64, 128, 256 };
            var data = new byte[sizes.Length][];
            for (int i = 0; i < sizes.Length; i++) data[i] = Render(sizes[i]);
            string assets = Path.Combine(root, "web", "brand");
            Directory.CreateDirectory(assets);
            File.WriteAllBytes(Path.Combine(assets, "app-icon.png"), data[data.Length - 1]);
            using (var writer = new BinaryWriter(File.Create(Path.Combine(root, "app.ico"))))
            {
                writer.Write((ushort)0); writer.Write((ushort)1); writer.Write((ushort)sizes.Length);
                int offset = 6 + 16 * sizes.Length;
                for (int i = 0; i < sizes.Length; i++)
                {
                    writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                    writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                    writer.Write((byte)0); writer.Write((byte)0); writer.Write((ushort)1); writer.Write((ushort)32);
                    writer.Write(data[i].Length); writer.Write(offset); offset += data[i].Length;
                }
                foreach (byte[] item in data) writer.Write(item);
            }
            File.Copy(Path.Combine(root, "app.ico"), Path.Combine(assets, "canvas-brand-v1.ico"), true);
            Console.WriteLine("Brand assets rendered: 16/24/32/48/64/128/256 px; transparent PNG and ICO.");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
    }
}
