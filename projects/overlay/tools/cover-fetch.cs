// SMTC cover fetcher (own implementation, no third-party code)
//
// Reads the current media session's thumbnail through the built-in Windows
// Runtime projection and writes the raw bytes to a file.  Uses the plain .NET
// Framework C# compiler (csc.exe), so no Windows SDK / NuGet package is needed.
//
// Why the manual wait loop instead of .AsTask(): this machine has no Windows
// SDK, so the union metadata (Windows.winmd) is missing.  Referencing the
// individual WinMetadata .winmd files works, but System.Runtime.WindowsRuntime
// insists on the "Windows" contract assembly.  Polling IAsyncOperation.Status
// and calling GetResults() avoids that dependency completely.
//
// Usage: cover-fetch.exe <allowed,list> <output.jpg> [--list]
//   exit 0 ok, 2 no session, 3 no thumbnail, 4 empty, 5 error
using System;
using System.IO;
using System.Text;
using System.Threading;
using Windows.Foundation;
using Windows.Media.Control;
using Windows.Storage.Streams;

public static class Program
{
    private static T Wait<T>(IAsyncOperation<T> op, int timeoutMs)
    {
        int waited = 0;
        while (op.Status == AsyncStatus.Started)
        {
            if (waited >= timeoutMs) throw new TimeoutException("async op timed out");
            Thread.Sleep(5);
            waited += 5;
        }
        if (op.Status != AsyncStatus.Completed)
            throw new InvalidOperationException("async op " + op.Status);
        return op.GetResults();
    }

    public static int Main(string[] args)
    {
        if (args.Length < 1) { Console.Error.WriteLine("usage: cover-fetch <allow> <out.jpg>"); return 5; }
        string[] allow = args[0].Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        string outPath = args.Length > 1 ? args[1] : "cover.jpg";
        bool listOnly = args.Length > 2 && args[2] == "--list";

        // csc / UTF-8 console: keep output ASCII so decoding never matters
        var mgr = Wait(GlobalSystemMediaTransportControlsSessionManager.RequestAsync(), 5000);
        GlobalSystemMediaTransportControlsSession pick = null;
        foreach (var s in mgr.GetSessions())
        {
            string id = (s.SourceAppUserModelId ?? "").ToLowerInvariant();
            if (listOnly) Console.WriteLine("session\t" + id);
            if (pick != null) continue;
            foreach (var a in allow)
            {
                if (a.Length > 0 && id.Contains(a.ToLowerInvariant())) { pick = s; break; }
            }
        }
        if (pick == null) { Console.WriteLine("no-session"); return 2; }
        if (listOnly) return 0;

        var props = Wait(pick.TryGetMediaPropertiesAsync(), 5000);
        Console.WriteLine("title\t" + props.Title);
        var thumb = props.Thumbnail;
        if (thumb == null) { Console.WriteLine("no-thumb"); return 3; }

        var stream = Wait(thumb.OpenReadAsync(), 5000);
        long size = (long)stream.Size;
        if (size <= 0 || size > 16 * 1024 * 1024) { Console.WriteLine("bad-size " + size); return 4; }

        var reader = new DataReader(stream);
        uint loaded = Wait(reader.LoadAsync((uint)size), 5000);
        var bytes = new byte[loaded];
        reader.ReadBytes(bytes);
        reader.Dispose();
        stream.Dispose();
        if (loaded == 0) { Console.WriteLine("empty"); return 4; }

        string dir = Path.GetDirectoryName(Path.GetFullPath(outPath));
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        File.WriteAllBytes(outPath, bytes);
        Console.WriteLine("wrote\t" + loaded);
        return 0;
    }
}
