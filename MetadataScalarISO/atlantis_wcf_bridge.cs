using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net;
using System.ServiceModel;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using Atlantis;
using Atlantis.Contracts;

internal static class AtlantisWcfBridge
{
    private const string Host = "rf3pvwb460.rf3prod.mfg.intel.com";
    private const string Spn = "net.tcp/rf3pvwb460.rf3prod.mfg.intel.com";
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };

    private static NetTcpBinding Binding()
    {
        var binding = new NetTcpBinding(SecurityMode.Transport);
        binding.Security.Transport.ClientCredentialType = TcpClientCredentialType.Windows;
        binding.TransferMode = TransferMode.StreamedResponse;
        binding.ListenBacklog = 32;
        binding.MaxConnections = 32;
        binding.MaxReceivedMessageSize = Int32.MaxValue;
        binding.ReaderQuotas.MaxDepth = 32;
        binding.ReaderQuotas.MaxStringContentLength = Int32.MaxValue;
        binding.ReaderQuotas.MaxArrayLength = Int32.MaxValue;
        binding.ReaderQuotas.MaxBytesPerRead = Int32.MaxValue;
        binding.ReaderQuotas.MaxNameTableCharCount = Int32.MaxValue;
        binding.OpenTimeout = TimeSpan.FromMinutes(2);
        binding.CloseTimeout = TimeSpan.FromMinutes(2);
        binding.SendTimeout = TimeSpan.FromMinutes(5);
        binding.ReceiveTimeout = TimeSpan.FromMinutes(5);
        return binding;
    }

    private static EndpointAddress Endpoint(int port, string service)
    {
        var address = new Uri(String.Format("net.tcp://{0}:{1}/Atlantis.{2}", Host, port, service));
        return new EndpointAddress(address, EndpointIdentity.CreateSpnIdentity(Spn));
    }

    private static ChannelFactory<T> Factory<T>(int port, string service) where T : class
    {
        var factory = new ChannelFactory<T>(Binding(), Endpoint(port, service));
        factory.Credentials.Windows.ClientCredential = CredentialCache.DefaultNetworkCredentials;
        return factory;
    }

    private static void Close(IClientChannel channel, ICommunicationObject factory)
    {
        try { if (channel != null) channel.Close(); } catch { if (channel != null) channel.Abort(); }
        try { if (factory != null) factory.Close(); } catch { if (factory != null) factory.Abort(); }
    }

    private static object Projects()
    {
        var factory = Factory<IConfigurationService>(6200, "ConfigurationService");
        IConfigurationService service = null;
        try
        {
            service = factory.CreateChannel();
            return service.GetValidProjects().ToArray();
        }
        finally { Close(service as IClientChannel, factory); }
    }

    private static object Libraries(string project)
    {
        var factory = Factory<IConfigurationService>(6200, "ConfigurationService");
        IConfigurationService service = null;
        try
        {
            service = factory.CreateChannel();
            return service.GetLibraryNames(project, true).ToArray();
        }
        finally { Close(service as IClientChannel, factory); }
    }

    private static TestRowCatalogFindQuerykey Query(string project, string rowPattern, string structurePattern)
    {
        return new TestRowCatalogFindQuerykey
        {
            TestRowName = rowPattern,
            StructureName = structurePattern,
            DBNames = new List<string> { project }
        };
    }

    private static object Rows(string project, string library)
    {
        var configFactory = Factory<IConfigurationService>(6200, "ConfigurationService");
        IConfigurationService config = null;
        string[] prefixes;
        try
        {
            config = configFactory.CreateChannel();
            prefixes = new[] {
                config.GetTestRowNamePrefix(project, library, false),
                config.GetTestRowNamePrefix(project, library, true)
            }.Where(p => !String.IsNullOrWhiteSpace(p)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        }
        finally { Close(config as IClientChannel, configFactory); }

        var catalogFactory = Factory<ICatalogService>(6300, "CatalogService");
        ICatalogService catalog = null;
        try
        {
            catalog = catalogFactory.CreateChannel();
            var rows = new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);
            foreach (string prefix in prefixes)
            {
                foreach (TestRowSummary item in catalog.GetTestRowInfo(Query(project, Regex.Escape(prefix) + ".*", ".*")))
                {
                    if (!String.Equals(item.Library, library, StringComparison.OrdinalIgnoreCase) ||
                        String.IsNullOrWhiteSpace(item.TestRowName))
                        continue;
                    rows[item.TestRowName] = new Dictionary<string, object>
                    {
                        { "TestRowName", item.TestRowName },
                        { "Library", item.Library },
                        { "Database", item.Database },
                        { "Status", item.StatusDesc },
                        { "StatusCode", item.Status },
                        { "LastModified", item.LastModifiedTime.ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture) },
                        { "Owner", item.Owner },
                        { "OwnerEmail", item.OwnerEmail },
                        { "ModifiedBy", item.LastModifiedBy },
                        { "Locked", item.Locked },
                        { "LockedBy", item.LockedBy },
                        { "BatchResult", item.BatchResult },
                        { "Description", item.Description },
                        { "Version", item.Version },
                        { "MetadataVersion", item.MetaDataVersion },
                        { "PadRowTemplate", item.PadrowTemplateName },
                        { "TemplatesUsed", item.TemplatesUsed },
                        { "TapeoutLabel", item.TapeoutLabel },
                        { "Url", item.Url },
                        { "BusType", item.BusType },
                        { "IsLatest", item.IsLatest }
                    };
                }
            }
            return rows.Values.OrderBy(r => r["TestRowName"]).ToArray();
        }
        finally { Close(catalog as IClientChannel, catalogFactory); }
    }

    private static object LibraryStructures(string project, string library)
    {
        var configFactory = Factory<IConfigurationService>(6200, "ConfigurationService");
        IConfigurationService config = null;
        string[] prefixes;
        try
        {
            config = configFactory.CreateChannel();
            prefixes = new[] {
                config.GetTestRowNamePrefix(project, library, false),
                config.GetTestRowNamePrefix(project, library, true)
            }.Where(p => !String.IsNullOrWhiteSpace(p)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        }
        finally { Close(config as IClientChannel, configFactory); }

        var catalogFactory = Factory<ICatalogService>(6300, "CatalogService");
        ICatalogService catalog = null;
        try
        {
            catalog = catalogFactory.CreateChannel();
            var result = new Dictionary<string, Dictionary<string, object>>(StringComparer.OrdinalIgnoreCase);
            foreach (string prefix in prefixes)
            {
                foreach (TestRowCatalogFindStruct item in catalog.GetTestRowStructInfo(
                             Query(project, Regex.Escape(prefix) + ".*", ".*")))
                {
                    if (!String.Equals(item.Library, library, StringComparison.OrdinalIgnoreCase) ||
                        String.IsNullOrWhiteSpace(item.StructureName))
                        continue;
                    string key = item.TestRowName + "\0" + item.StructureName + "\0" + item.PadRowName;
                    result[key] = new Dictionary<string, object>
                    {
                        { "TestRowName", item.TestRowName },
                        { "Library", item.Library },
                        { "Database", item.Database },
                        { "StructureName", item.StructureName },
                        { "Description", item.Description },
                        { "PadRowName", item.PadRowName }
                    };
                }
            }
            return result.Values.OrderBy(r => r["StructureName"]).ThenBy(r => r["TestRowName"]).ToArray();
        }
        finally { Close(catalog as IClientChannel, catalogFactory); }
    }

    private static object Structures(string project, string library, string testRow)
    {
        var factory = Factory<ICatalogService>(6300, "CatalogService");
        ICatalogService catalog = null;
        try
        {
            catalog = factory.CreateChannel();
            var result = new List<Dictionary<string, object>>();
            foreach (TestRowCatalogFindStruct item in catalog.GetTestRowStructInfo(
                         Query(project, "^" + Regex.Escape(testRow) + "$", ".*")))
            {
                if (!String.Equals(item.Library, library, StringComparison.OrdinalIgnoreCase) ||
                    !String.Equals(item.TestRowName, testRow, StringComparison.OrdinalIgnoreCase))
                    continue;
                result.Add(new Dictionary<string, object>
                {
                    { "TestRowName", item.TestRowName },
                    { "Library", item.Library },
                    { "Database", item.Database },
                    { "StructureName", item.StructureName },
                    { "Description", item.Description },
                    { "PadRowName", item.PadRowName }
                });
            }
            return result.OrderBy(r => r["StructureName"]).ToArray();
        }
        finally { Close(catalog as IClientChannel, factory); }
    }

    private static object Parameters(string project, string library, string testRow, string structuresJson)
    {
        var selected = Json.Deserialize<string[]>(structuresJson) ?? new string[0];
        var selectedSet = new HashSet<string>(selected, StringComparer.OrdinalIgnoreCase);
        var factory = Factory<ICatalogService>(6300, "CatalogService");
        ICatalogService catalog = null;
        try
        {
            catalog = factory.CreateChannel();
            var result = new List<Dictionary<string, object>>();
            foreach (TestRowCatalogFindParam item in catalog.GetTestRowParamInfo(
                         Query(project, "^" + Regex.Escape(testRow) + "$", ".*")))
            {
                if (!String.Equals(item.Library, library, StringComparison.OrdinalIgnoreCase) ||
                    !String.Equals(item.TestRowName, testRow, StringComparison.OrdinalIgnoreCase) ||
                    (selectedSet.Count > 0 && !selectedSet.Contains(item.StructureName)))
                    continue;
                result.Add(new Dictionary<string, object>
                {
                    { "TestRowName", item.TestRowName },
                    { "Library", item.Library },
                    { "Database", item.Database },
                    { "StructureName", item.StructureName },
                    { "PadRowName", item.PadRowName },
                    { "ParameterName", item.ParameterName },
                    { "ParameterValue", item.ParameterValue }
                });
            }
            return result;
        }
        finally { Close(catalog as IClientChannel, factory); }
    }

    private static void Main(string[] args)
    {
        try
        {
            object result;
            switch (args[0])
            {
                case "projects": result = Projects(); break;
                case "libraries": result = Libraries(args[1]); break;
                case "rows": result = Rows(args[1], args[2]); break;
                case "library-structures": result = LibraryStructures(args[1], args[2]); break;
                case "structures": result = Structures(args[1], args[2], args[3]); break;
                case "parameters": result = Parameters(args[1], args[2], args[3], args[4]); break;
                default: throw new ArgumentException("Unknown Atlantis operation.");
            }
            Console.WriteLine(Json.Serialize(result));
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.GetType().FullName + ": " + ex.Message);
            Environment.ExitCode = 1;
        }
    }
}
