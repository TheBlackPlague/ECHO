import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import {QueryCache, QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {ApiError} from "./api/echo-api";
import App from "./app/App";
import "./styles.css";


const queryClient = new QueryClient({
    defaultOptions: {
        queries: {
            staleTime: 5_000,
            refetchOnWindowFocus: true,
            retry: (failureCount, error) => error instanceof ApiError && error.status >= 400 && error.status < 500
                ? false
                : failureCount < 2,
        },
    },
    queryCache: new QueryCache(),
});

createRoot(document.getElementById("root")!).render(
    <StrictMode>
        <QueryClientProvider client={queryClient}>
            <App/>
        </QueryClientProvider>
    </StrictMode>,
);
