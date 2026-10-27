// 叠加层启动器：双击就起服务（黑窗口显示日志），顺手把控制面板开在浏览器里。
//   编译：tools\build-exe.cmd  →  Cosmolayor.exe（项目根目录）
//   参数：--no-open  只起服务，不开浏览器
//
// 关掉这个窗口 = 停服务（窗口里的 python 是子进程，会跟着结束）。
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;

static class Launcher
{
    const string Port = "8765";
    static readonly string BaseUrl = "http://127.0.0.1:" + Port;

    static void Main(string[] args)
    {
        try { Console.OutputEncoding = Encoding.UTF8; } catch { }
        string root = AppDomain.CurrentDomain.BaseDirectory;
        bool openPanel = Array.IndexOf(args, "--no-open") < 0;

        if (!File.Exists(Path.Combine(root, "server.py")))
        {
            Say("Cosmolayor.exe 要和 server.py 放在同一个文件夹里。");
            Pause();
            return;
        }

        try { Console.Title = "Cosmolayor · " + BaseUrl + "/control"; } catch { }

        if (Ping())
        {
            Say("Cosmolayor 已经在运行了：" + BaseUrl);
            Say("（控制面板 / OBS 地址不变，直接开浏览器用就行）");
            if (openPanel) OpenUrl(BaseUrl + "/control");
            Pause();
            return;
        }

        string py = FindPython();
        if (py == null)
        {
            Say("找不到 Python。");
            Say("装一个 Python 3，或者把 python.exe 所在目录加进 PATH 再试。");
            Pause();
            return;
        }

        Say("正在启动服务…（关掉这个窗口就是停止）");
        Say("");
        var psi = new ProcessStartInfo(py, "-u server.py");
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;          // 继承当前控制台：python 的日志直接打在窗口里
        Process child;
        try { child = Process.Start(psi); }
        catch (Exception e)
        {
            Say("起不来：" + e.Message);
            Pause();
            return;
        }

        // 等它就绪再开浏览器；没就绪也不拦着，用户能在窗口里看到报错
        var sw = Stopwatch.StartNew();
        bool ready = false;
        while (sw.ElapsedMilliseconds < 15000)
        {
            if (Ping()) { ready = true; break; }
            if (child.HasExited) break;
            Thread.Sleep(250);
        }
        if (ready)
        {
            Say("");
            Say("服务已就绪：" + BaseUrl + "/control");
            Say("OBS 浏览器源填：" + BaseUrl + "/index.html   （1920×1080，透明）");
            Say("");
            if (openPanel) OpenUrl(BaseUrl + "/control");
        }
        else if (child.HasExited)
        {
            Say("");
            Say("服务退出了（上面应该写了原因），按任意键关闭…");
            Pause();
            return;
        }

        child.WaitForExit();
        Console.WriteLine();
        Say("服务已停止。");
        Pause();
    }

    static void Say(string s)
    {
        Console.WriteLine(s);
    }

    static void Pause()
    {
        Console.WriteLine();
        Console.WriteLine("按任意键关闭窗口…");
        try { Console.ReadKey(true); } catch { }
    }

    static bool Ping()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(BaseUrl + "/api/health");
            req.Timeout = 900;
            req.ReadWriteTimeout = 900;
            using (var resp = (HttpWebResponse)req.GetResponse())
                return resp.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    static string FindPython()
    {
        // 1) 发布包里自带的解释器（免安装：解压就能跑）
        var bundled = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "python", "python.exe");
        if (File.Exists(bundled)) return bundled;

        // 2) 本机装的 Python 3.12
        var local = Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? "";
        var guess = Path.Combine(local, @"Programs\Python\Python312\python.exe");
        if (File.Exists(guess)) return guess;

        // 3) PATH
        var path = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var dir in path.Split(';'))
        {
            if (dir.Trim().Length == 0) continue;
            try
            {
                var full = Path.Combine(dir.Trim(), "python.exe");
                if (File.Exists(full)) return full;
            }
            catch { }
        }
        return null;
    }

    static void OpenUrl(string url)
    {
        try { Process.Start(url); } catch { }
    }
}
