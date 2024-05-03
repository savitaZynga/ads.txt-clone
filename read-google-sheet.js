const { google } = require('googleapis');
const fs = require('fs');

// Load the service account key JSON file.
// const serviceAccount = JSON.parse(process.env.SERVICE_ACCOUNT_KEY);
const serviceAccount = require('./service-account.json')


const spreadsheetId = '19r8IWr7xwpP2NhtQzs_5m00FaKbGcOfN95uiyr55zH4';
// Define the scopes for the Google Sheets API.
const scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly'];

// Authenticate with the Google Sheets API.
const auth = new google.auth.JWT(serviceAccount.client_email, null, serviceAccount.private_key, scopes);

const sheets = google.sheets({ version: 'v4', auth });

sheets.spreadsheets.values.get(
	{
		spreadsheetId: spreadsheetId,
		range: 'Sheet1',
	},
	(err, res) => {
		if (err) return console.log('The API returned an error: ' + err);
		const rows = res.data.values;
		if (rows.length) {
			console.log('Data from Google Sheet:');
			let data = '';
			rows.map((row) => {
				console.log(row.join(', '));
				data += row.join(', ') + '\n';
			});
			fs.writeFileSync('./app-ads.txt', data);
		} else {
			console.log('No data found.');
		}
	}
);
