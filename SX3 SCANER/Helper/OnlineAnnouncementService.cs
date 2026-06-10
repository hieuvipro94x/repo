using Newtonsoft.Json;
using SX3_SCANER.Model;
using System;
using System.Diagnostics;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Threading;

namespace SX3_SCANER.Helper
{
    internal sealed class OnlineAnnouncementService : IDisposable
    {
        private const string AnnouncementApiUrl =
            "https://raw.githubusercontent.com/hieuvipro94x/sx3-scanner-release/main/announcement.json";

        private readonly HttpClient _httpClient;
        private readonly DispatcherTimer _refreshTimer;
        private readonly Dispatcher _dispatcher;

        private int _isChecking;
        private bool _isStarted;
        private bool _isDisposed;
        private string _lastKey = string.Empty;

        public event EventHandler<AnnouncementInfo> AnnouncementChanged;

        public OnlineAnnouncementService()
        {
            _dispatcher = Dispatcher.CurrentDispatcher;

            _httpClient = new HttpClient
            {
                Timeout = TimeSpan.FromSeconds(8)
            };

            _httpClient.DefaultRequestHeaders.UserAgent.ParseAdd("SX3Scanner/6.8.0");
            _httpClient.DefaultRequestHeaders.CacheControl = new CacheControlHeaderValue
            {
                NoCache = true,
                NoStore = true,
                MaxAge = TimeSpan.Zero
            };

            _refreshTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(30)
            };

            _refreshTimer.Tick += async (s, e) => await LoadAnnouncementAsync();
        }

        public async void Start()
        {
            if (_isDisposed || _isStarted)
                return;

            _isStarted = true;

            await LoadAnnouncementAsync();

            if (!_isDisposed)
                _refreshTimer.Start();
        }

        public async Task LoadAnnouncementAsync()
        {
            if (_isDisposed ||
                Interlocked.CompareExchange(ref _isChecking, 1, 0) != 0)
                return;

            try
            {
                string url = AnnouncementApiUrl + "?_=" +
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

                using (HttpResponseMessage response =
                    await _httpClient.GetAsync(url).ConfigureAwait(false))
                {
                    if (!response.IsSuccessStatusCode)
                    {
                        Debug.WriteLine("Announcement failed: " + response.StatusCode);
                        return;
                    }

                    string json = await response.Content
                        .ReadAsStringAsync()
                        .ConfigureAwait(false);

                    AnnouncementInfo announcement =
                        JsonConvert.DeserializeObject<AnnouncementInfo>(json);

                    if (announcement == null)
                        return;

                    NormalizeAnnouncement(announcement);

                    string newKey = BuildAnnouncementKey(announcement);

                    await _dispatcher.InvokeAsync(() =>
                    {
                        if (_isDisposed)
                            return;

                        _refreshTimer.Interval =
                            TimeSpan.FromSeconds(announcement.PollSeconds);

                        if (!announcement.Enabled)
                            return;

                        bool changed =
                            !string.Equals(_lastKey, newKey, StringComparison.Ordinal);

                        if (!changed && !announcement.ForceUpdate)
                            return;

                        _lastKey = newKey;
                        AnnouncementChanged?.Invoke(this, announcement);
                    });
                }
            }
            catch (Exception ex)
            {
                Debug.WriteLine("Announcement error: " + ex.Message);
            }
            finally
            {
                Interlocked.Exchange(ref _isChecking, 0);
            }
        }

        private static string BuildAnnouncementKey(AnnouncementInfo announcement)
        {
            if (!string.IsNullOrWhiteSpace(announcement.UpdatedAt))
                return "updatedAt:" + announcement.UpdatedAt.Trim();

            if (!string.IsNullOrWhiteSpace(announcement.Version))
                return "version:" + announcement.Version.Trim();

            return JsonConvert.SerializeObject(announcement);
        }

        private static void NormalizeAnnouncement(AnnouncementInfo announcement)
        {
            announcement.Mode = NormalizeText(announcement.Mode, "single").ToLowerInvariant();
            announcement.Level = NormalizeLevel(announcement.Level);
            announcement.Title = NormalizeText(announcement.Title, "THÔNG BÁO HỆ THỐNG");
            announcement.Message = announcement.Message?.Trim() ?? string.Empty;
            announcement.UpdatedAt = announcement.UpdatedAt?.Trim() ?? string.Empty;
            announcement.Version = announcement.Version?.Trim() ?? string.Empty;
            announcement.BackgroundColor = announcement.BackgroundColor?.Trim() ?? string.Empty;
            announcement.ForegroundColor = announcement.ForegroundColor?.Trim() ?? string.Empty;
            announcement.CreatedBy = announcement.CreatedBy?.Trim() ?? string.Empty;

            announcement.PollSeconds = Clamp(announcement.PollSeconds, 10, 300, 30);
            announcement.RotateSeconds = Clamp(announcement.RotateSeconds, 3, 60, 10);
            announcement.AutoHideSeconds = Math.Max(0, announcement.AutoHideSeconds);
            announcement.Priority = Math.Max(0, announcement.Priority);

            if (announcement.Messages == null)
                announcement.Messages = new System.Collections.Generic.List<AnnouncementMessageInfo>();

            for (int i = announcement.Messages.Count - 1; i >= 0; i--)
            {
                var msg = announcement.Messages[i];

                if (msg == null || string.IsNullOrWhiteSpace(msg.Message))
                {
                    announcement.Messages.RemoveAt(i);
                    continue;
                }

                msg.Level = NormalizeLevel(msg.Level);
                msg.Title = NormalizeText(msg.Title, "THÔNG BÁO HỆ THỐNG");
                msg.Message = msg.Message.Trim();
                msg.BackgroundColor = msg.BackgroundColor?.Trim() ?? string.Empty;
                msg.ForegroundColor = msg.ForegroundColor?.Trim() ?? string.Empty;

                if (msg.AutoHideSeconds.HasValue)
                    msg.AutoHideSeconds = Math.Max(0, msg.AutoHideSeconds.Value);
            }
        }

        private static string NormalizeText(string value, string fallback)
        {
            return string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();
        }

        private static int Clamp(int value, int min, int max, int fallback)
        {
            if (value <= 0)
                return fallback;

            if (value < min)
                return min;

            if (value > max)
                return max;

            return value;
        }

        private static string NormalizeLevel(string level)
        {
            switch ((level ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "warning":
                case "error":
                case "success":
                    return level.Trim().ToLowerInvariant();

                default:
                    return "info";
            }
        }

        public void Dispose()
        {
            if (_isDisposed)
                return;

            _isDisposed = true;
            _refreshTimer.Stop();
            _httpClient.Dispose();
        }
    }
}