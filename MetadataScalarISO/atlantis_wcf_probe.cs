using System;
using System.Collections;
using System.IO;
using System.Net;
using System.Reflection;
using System.ServiceModel;
using System.Collections.Generic;
using Atlantis;
using Atlantis.Contracts;

internal static class AtlantisWcfProbe
{
    private static string assemblyDirectory;

    private static Assembly ResolveAssembly(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name + ".dll";
        string path = Path.Combine(assemblyDirectory, name);
        return File.Exists(path) ? Assembly.LoadFrom(path) : null;
    }

    private static void Main(string[] args)
    {
        if (args.Length != 1)
        {
            Console.Error.WriteLine("Usage: AtlantisWcfProbe <Atlantis ClickOnce assembly directory>");
            Environment.ExitCode = 2;
            return;
        }

        assemblyDirectory = args[0];
        AppDomain.CurrentDomain.AssemblyResolve += ResolveAssembly;
        var binding = new NetTcpBinding(SecurityMode.Transport);
        binding.Security.Transport.ClientCredentialType = TcpClientCredentialType.Windows;
        binding.TransferMode = TransferMode.StreamedResponse;
        binding.ListenBacklog = 32;
        binding.MaxConnections = 32;
        binding.MaxReceivedMessageSize = int.MaxValue;
        binding.ReaderQuotas.MaxDepth = 32;
        binding.ReaderQuotas.MaxStringContentLength = int.MaxValue;
        binding.ReaderQuotas.MaxArrayLength = int.MaxValue;
        binding.ReaderQuotas.MaxBytesPerRead = int.MaxValue;
        binding.ReaderQuotas.MaxNameTableCharCount = int.MaxValue;
        binding.OpenTimeout = TimeSpan.FromMinutes(30);
        binding.CloseTimeout = TimeSpan.FromMinutes(30);
        binding.SendTimeout = TimeSpan.FromMinutes(30);
        binding.ReceiveTimeout = TimeSpan.FromMinutes(30);

        var endpoint = new EndpointAddress(
            new Uri("net.tcp://rf3pvwb460.rf3prod.mfg.intel.com:6200/Atlantis.ConfigurationService"),
            EndpointIdentity.CreateSpnIdentity("net.tcp/rf3pvwb460.rf3prod.mfg.intel.com"));
        var factory = new ChannelFactory<IConfigurationService>(binding, endpoint);
        factory.Credentials.Windows.ClientCredential = CredentialCache.DefaultNetworkCredentials;
        IConfigurationService service = null;

        try
        {
            service = factory.CreateChannel();
            var projects = service.GetValidProjects();
            Console.WriteLine("Windows-authenticated Atlantis configuration service call succeeded.");
            Console.WriteLine("Available projects/nodes: " + projects.Count);
            int shown = Math.Min(projects.Count, 10);
            for (int i = 0; i < shown; i++)
            {
                Console.WriteLine("  " + projects[i]);
            }

            string selectedProject = projects.Count > 0 ? projects[0] : null;
            for (int i = 0; i < projects.Count; i++)
            {
                if (String.Equals(projects[i], "X81", StringComparison.OrdinalIgnoreCase))
                    selectedProject = projects[i];
            }

            if (selectedProject != null)
            {
                foreach (bool includeNonCurrent in new[] { false, true })
                {
                    var libraries = service.GetLibraryNames(selectedProject, includeNonCurrent);
                    Console.WriteLine("Libraries in " + selectedProject + " (non-current=" + includeNonCurrent + "): " + libraries.Count);
                    int libraryCount = Math.Min(libraries.Count, 20);
                    for (int i = 0; i < libraryCount; i++)
                        Console.WriteLine("  " + libraries[i]);
                }
            }

            foreach (var method in typeof(ICatalogService).GetMethods())
            {
                if (method.Name.Contains("Search") || method.Name.Contains("TestRowInfo"))
                    Console.WriteLine("Catalog API: " + method);
            }

            foreach (string typeName in new[] { "Atlantis.SearchNEWQueryKey", "Atlantis.TestRowCatalogFindQuerykey" })
            {
                Type queryType = typeof(ICatalogService).Assembly.GetType(typeName, false);
                if (queryType != null)
                {
                    Console.WriteLine("Query type: " + queryType.FullName);
                    foreach (var property in queryType.GetProperties())
                        Console.WriteLine("  " + property.PropertyType + " " + property.Name);
                }
            }

            var catalogEndpoint = new EndpointAddress(
                new Uri("net.tcp://rf3pvwb460.rf3prod.mfg.intel.com:6300/Atlantis.CatalogService"),
                EndpointIdentity.CreateSpnIdentity("net.tcp/rf3pvwb460.rf3prod.mfg.intel.com"));
            var catalogFactory = new ChannelFactory<ICatalogService>(binding, catalogEndpoint);
            catalogFactory.Credentials.Windows.ClientCredential = CredentialCache.DefaultNetworkCredentials;
            var catalog = catalogFactory.CreateChannel();
            string selectedLibrary = "scratch4x81z_x81_lay";
            string rowPrefix = service.GetTestRowNamePrefix(selectedProject, selectedLibrary, false);
            string customRowPrefix = service.GetTestRowNamePrefix(selectedProject, selectedLibrary, true);
            Console.WriteLine("Row prefix in " + selectedLibrary + ": " + rowPrefix + " (custom=" + customRowPrefix + ")");
            var query = new TestRowCatalogFindQuerykey();
            query.TestRowName = (rowPrefix ?? "x81z") + ".*";
            query.StructureName = ".*";
            query.DBNames = new List<string> { "X81" };
            var rows = catalog.GetTestRowInfo(query);
            int rowsSeen = 0;
            TestRowSummary sampleRow = null;
            foreach (var row in rows)
            {
                if (!String.Equals(row.Library, selectedLibrary, StringComparison.OrdinalIgnoreCase))
                    continue;
                if (sampleRow == null)
                    sampleRow = row;
                if (rowsSeen < 10)
                    Console.WriteLine("Row: " + row.TestRowName + " | " + row.Library + " | " + row.Database);
                rowsSeen++;
            }
            Console.WriteLine("Catalog rows in selected library: " + rowsSeen);
            if (sampleRow != null)
            {
                MethodInfo method = typeof(ICatalogService).GetMethod("GetTestRowParamInfo");
                Type keyType = method.GetParameters()[0].ParameterType;
                object key = Activator.CreateInstance(keyType);
                SetIfPresent(key, "TestRowName", sampleRow.TestRowName);
                SetIfPresent(key, "StructureName", ".*");
                SetIfPresent(key, "DBNames", new List<string> { sampleRow.Database });
                Console.WriteLine("Parameter query: " + method);
                object response = method.Invoke(catalog, new[] { key });
                int valuesSeen = 0;
                foreach (object item in (IEnumerable)response)
                {
                    if (valuesSeen < 12)
                    {
                        var properties = item.GetType().GetProperties();
                        string summary = String.Join(" | ", Array.ConvertAll(properties, p =>
                            p.Name + "=" + Convert.ToString(p.GetValue(item, null))));
                        Console.WriteLine("  Parameter data: " + summary);
                    }
                    valuesSeen++;
                }
                Console.WriteLine("Parameter records in sample row: " + valuesSeen);

                MethodInfo structureMethod = typeof(ICatalogService).GetMethod("GetTestRowStructInfo");
                object structureResponse = structureMethod.Invoke(catalog, new[] { key });
                int structuresSeen = 0;
                foreach (object item in (IEnumerable)structureResponse)
                {
                    if (structuresSeen < 5)
                    {
                        var properties = item.GetType().GetProperties();
                        string summary = String.Join(" | ", Array.ConvertAll(properties, p =>
                            p.Name + "=" + Convert.ToString(p.GetValue(item, null))));
                        Console.WriteLine("  Structure data: " + summary);
                    }
                    structuresSeen++;
                    if (structuresSeen == 5)
                        break;
                }
                Console.WriteLine("Structure records sampled: " + structuresSeen);
            }
            ((IClientChannel)catalog).Close();
            catalogFactory.Close();

            ((IClientChannel)service).Close();
            factory.Close();
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.GetType().FullName + ": " + ex.Message);
            if (service is IClientChannel)
            {
                ((IClientChannel)service).Abort();
            }
            factory.Abort();
            Environment.ExitCode = 1;
        }
    }

    private static void SetIfPresent(object target, string propertyName, object value)
    {
        PropertyInfo property = target.GetType().GetProperty(propertyName);
        if (property != null && property.CanWrite && value != null)
            property.SetValue(target, value, null);
    }
}
