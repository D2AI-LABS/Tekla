// TeklaConnectionService.cs — Wrapper for Tekla Structures connection management
using System;
using Tekla.Structures.Model;

namespace TeklaExtractor.Services
{
    public static class TeklaConnectionService
    {
        public static Model Connect()
        {
            var model = new Model();
            if (!model.GetConnectionStatus())
                throw new InvalidOperationException("Cannot connect to Tekla Structures. Ensure Tekla is running with a model open.");
            return model;
        }

        public static bool IsConnected()
        {
            try { return new Model().GetConnectionStatus(); }
            catch { return false; }
        }
    }
}
