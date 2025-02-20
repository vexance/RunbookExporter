import azure.identity, azure.mgmt.automation
import argparse, os, termcolor, traceback, typing
import requests

ACCESS_TOKEN = None

def print_status(status_type: str, text: str) -> None:
    '''Print text to terminal / append to file if specified. Status Types supported: `x`: red, `-`: red,`!`: yellow, `*`: blue, `+`: green'''
    if status_type != '':
        status_type = f'[{status_type.lower()}]'

    color = 'white'
    if status_type == '[-]':
        color = 'red'
    elif status_type == '[x]':
        color = 'magenta'
    elif status_type == '[!]':
        color = 'yellow'
    elif status_type == '[*]':
        color = 'blue'
    elif status_type == '[+]':
        color = 'green'
    print(f'{termcolor.colored(status_type, color)} {text}')

    if outfile:
        outfile.write(f'{termcolor.colored(status_type, color)} {text}\n')
    
    return  None


def get_credentials(sub_id:str = None) -> azure.identity.AzureCliCredential:
    '''Grabs authentication token from CLI context (run `az login` prior to using script). Yes, this is a function just wrapping another function...'''
    try:

        if sub_id:
            tenant=os.environ.get('AZURE_DIRECTORY_ID')
            app=os.environ.get('AZURE_APPLICATION_ID')
            secret=os.environ.get('AZURE_KEY_VALUE')
            sub=sub_id

            endpoint='https://management.azure.com'
            cred = azure.identity.ClientSecretCredential(tenant_id=tenant, client_id=app, client_secret=secret)
            access_token = cred.get_token(f'{endpoint}/.default')
            creds = f'{access_token.token}'

        else:
            creds = azure.identity.AzureCliCredential()
    except Exception as err:
        print_status('x', f'Unable to locate credentials - did you try running `az login` in this terminal?\n{err}')
        exit()

    return creds


def get_automation_accounts(sub_id: str) -> list:
    '''Returns a list<dict> containing all automation account ids within a provided azure subscription. Resulting dictionaries have two fields: `AutomationAccountName` and `ResourceGroup`'''
    print_status('*', f'Listing automation accounts (ResourceGroup:AutomationAccountName) within subscription \'{sub_id}\'')
    creds = get_credentials()
    client = azure.mgmt.automation.AutomationClient(creds, sub_id)

    try:
        itr = client.automation_account.list()
    except Exception as err:
        print_status('x', f'{err}')
        return []
    
    accounts = []
    for acc in list(itr):
        parsed = acc.id.split('/')
        # Get indexes of resource group, account name from the Id string
        res_grp = parsed.index('resourceGroups') + 1
        name = parsed.index('automationAccounts') + 1
        print_status('+', f'{parsed[res_grp]}:{parsed[name]}')
        accounts.append({
            'AutomationAccountName': parsed[name],
            'ResourceGroup': parsed[res_grp]
        })

    if len(accounts) == 0:
        print_status('*', 'No automation accounts found')

    return accounts


def get_automation_runbooks(sub_id: str, res_grp: str, name: str) -> list:
    ''''Returns a list<dict> containing all automation account ids within a provided azure subscription. Resulting dictionaries have two fields: `AutomationAccountName`, `ResourceGroup`, `RunbookType`, and `RunbookName`'''
    print_status('*', f'Finding runbooks with Automation Account \'{name}\'')
    creds = get_credentials()
    client = azure.mgmt.automation.AutomationClient(creds, sub_id)

    try:
        itr = client.runbook.list_by_automation_account(res_grp, name)
    except Exception as err:
        print_status('x', f'{err}')
        return []

    runbooks = []
    for rbk in list(itr):
        print_status('+', f'{rbk.name} - {rbk.runbook_type}')
        runbooks.append({
            'AutomationAccountName': name,
            'ResourceGroup': res_grp,
            'RunbookType': rbk.type,
            'RunbookName': rbk.name,
            'RunbookType': rbk.runbook_type
        })

    if len(runbooks) == 0:
        print_status('*', 'No runbooks found')
    
    return runbooks


def get_runbook_contents(sub_id: str, res_grp: str, acc_name: str, rbk_name: str) -> typing.IO:
    '''Attempts to download content for a specified azure automation runbook'''
    global ACCESS_TOKEN
    creds = get_credentials()
    client = (creds, sub_id)

    print_status('*', f'Attempting to export \'{rbk_name}\'')
    try:
        content = client.runbook.get_content(res_grp, acc_name, rbk_name)
    except Exception as err:
        
        try:
            if ACCESS_TOKEN == None:
                ACCESS_TOKEN = get_credentials(sub_id=sub_id)
            
            endpoint = f'https://management.azure.com/subscriptions/{sub_id}/resourceGroups/{res_grp}/providers/Microsoft.Automation/automationAccounts/{acc_name}/runbooks/{rbk_name}/content?api-version=2023-11-01'
            res = requests.get(endpoint, headers={'Authorization': f'Bearer {ACCESS_TOKEN}'})
            content = res.text

        except Exception as err:
        
            print_status('x', f'{err}')
        

        
    return content


def export_runbooks(sub_id: str, args: argparse.Namespace) -> None:
    '''Attempt to download contents of all automation runbooks readable within the provided subscription'''
    try:
        # Collect automation accounts within the subscription
        accounts = get_automation_accounts(sub_id)

        # List automation runbooks published to each account
        runbooks = []
        for acc in accounts:
            books = get_automation_runbooks(sub_id, acc['ResourceGroup'], acc['AutomationAccountName'])
            runbooks.extend(books)

        if args.download_directory:
            if not os.path.isdir(args.download_directory):
                os.mkdir(f'{args.download_directory}')

            for book in runbooks:
                content = get_runbook_contents(sub_id, book['ResourceGroup'], book['AutomationAccountName'], book['RunbookName'])

                if not content:
                    continue
                
                # ToDo: Right now everything exports as a powershell (ps1) or python (py) script. Need to figure out various RunbookTypes and expected file extensions from them
                ext = {
                    'Powershell': 'ps1',
                    'GraphPowershell': 'ps1',
                    'Script': 'ps1',
                    'Python3': 'py',
                    'Python2': 'py'
                }

                filepath = f'{args.download_directory}/{sub_id}_{book["RunbookName"]}.{ext.get(book["RunbookType"], "ps1")}'
                with open(filepath, 'w') as exportfile:
                    exportfile.write(content)
    
    except Exception as err:
        print_status('x', f'{err}')

    return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser('RunbookExporter', 'python3 runbook_exporter.py -sf ./subscriptions_in_scope.txt -d ./Exports -o ./runbook_exporter.log')
    parser.add_argument('-s', '--subscription-id', required=False, default=None, help='Single subscription id to retrieve runbooks from [exclusive with -sf]')
    parser.add_argument('-sf', '--subscription-file', required=False, default=None, help='File containing list of subscription ids to inspect, one per line [exclusive with -s]')
    parser.add_argument('-o', '--outfile', required=False, default=None, help='File to append console output to [default: None]')
    parser.add_argument('-d', '--download-directory', required=False, default='./Testing', help='Directory to export runbook files to [default: ./RunbookExports]')
    args = parser.parse_args()

    global outfile 
    outfile = outfile = open(args.outfile, 'w+') if (args.outfile) else None

    if not any([args.subscription_id, args.subscription_file]):
        print_status('!', 'A subscription id OR input file must be specified, exiting! [try -s or -sf]')
        parser.print_help()
        exit()
    if all([args.subscription_id, args.subscription_file]):
        print_status('!', 'Both subscription id and input file specified, defaulting to single subscription!')
        args.subscription_file = None

    try:
        subscriptions = []
        if (args.subscription_file):
            with open(args.subscription_file, 'r') as infile:
                for line in infile:
                    sub = line.strip()
                    subscriptions.append(sub)
        else:
            subscriptions = [args.subscription_id]

        for sub in subscriptions:
            print_status('*', f'Attempting to locate and export automation runbooks within subscription \'{sub}\'')
            export_runbooks(sub, args)

    except Exception as err:
        print_status('x', f'{err}')
        traceback.print_exc(file=outfile)
        if outfile:
            outfile.close()
    
    if outfile:
        outfile.close()

