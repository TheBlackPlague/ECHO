import echoIcon from "../assets/echo-icon.svg";


export function Logo() {
    return (
        <div className="brand" aria-label="ECHO — Emergency Copy Held Offsite">
            <img src={echoIcon} alt="ECHO" width={40} height={40}/>
            <div className="brand-copy"><strong>ECHO</strong><span>Emergency Copy Held Offsite</span></div>
        </div>
    );
}
