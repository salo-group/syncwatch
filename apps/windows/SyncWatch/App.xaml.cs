using System.Windows;
using SyncWatch.Domain;

namespace SyncWatch;

public partial class App : Application
{
    public PlaybackScope PlaybackScope { get; } = PlaybackScope.Create();
}
