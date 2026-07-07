// JsonHelper.cs — JSON read/write utilities for BIM element exchange
using System;
using System.IO;
using Newtonsoft.Json;

namespace TeklaExtractor.Utilities
{
    public static class JsonHelper
    {
        public static T ReadJson<T>(string path)
        {
            if (!File.Exists(path)) return default;
            var raw = File.ReadAllText(path, System.Text.Encoding.UTF8);
            return JsonConvert.DeserializeObject<T>(raw);
        }

        public static void WriteJson<T>(string path, T data)
        {
            var json = JsonConvert.SerializeObject(data, Formatting.Indented);
            File.WriteAllText(path, json, System.Text.Encoding.UTF8);
        }
    }
}
